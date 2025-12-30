# -*- coding: utf-8 -*-
from odoo import api, fields, models, _
from odoo.exceptions import UserError


class LotTraceabilityLine(models.Model):
    _name = "lot.traceability.line"
    _description = "Lot Traceability Line"
    _order = "date asc, id asc"

    wizard_id = fields.Many2one("lot.traceability.wizard", ondelete="cascade", index=True, required=True)

    date = fields.Datetime(string="Date", readonly=True)
    lot_id = fields.Many2one("stock.lot", string="Lot/Serial", readonly=True)
    product_id = fields.Many2one("product.product", string="Product", readonly=True)
    qty = fields.Float(string="Qty", readonly=True)
    uom_id = fields.Many2one("uom.uom", string="UoM", readonly=True)

    picking_id = fields.Many2one("stock.picking", string="Picking", readonly=True)
    picking_type_code = fields.Selection(
        [("incoming", "Incoming"), ("outgoing", "Outgoing"), ("internal", "Internal"), ("mrp_operation", "Manufacturing")],
        string="Type",
        readonly=True,
    )
    partner_id = fields.Many2one("res.partner", string="Partner", readonly=True)
    location_from_id = fields.Many2one("stock.location", string="From", readonly=True)
    location_to_id = fields.Many2one("stock.location", string="To", readonly=True)

    origin_ref = fields.Char(string="Origin", readonly=True)
    production_id = fields.Many2one("mrp.production", string="MO", readonly=True)

    # أهم إضافة: ربط الـ finished lots بالـ raw lots اللي اتعملت منها
    source_raw_lot_ids = fields.Many2many("stock.lot", string="Source Raw Lots", readonly=True)


class LotTraceabilityWizard(models.TransientModel):
    _name = "lot.traceability.wizard"
    _description = "Traceability Report Wizard"

    invoice_id = fields.Many2one("account.move", string="Invoice", required=True)
    # اختياري: لو عايز تسمح بالتصفية اليدوية، بس هنخليه مش مطلوب
    lot_ids = fields.Many2many("stock.lot", string="Lots/Serials (Optional)")

    line_ids = fields.One2many("lot.traceability.line", "wizard_id", string="Lines", readonly=True)
    moves_count = fields.Integer(string="Moves Count", compute="_compute_moves_count", readonly=True)

    @api.depends("line_ids")
    def _compute_moves_count(self):
        for w in self:
            w.moves_count = len(w.line_ids)

    # ---------------------------------------------------------------------
    # Helpers (استخراج اللوتات من الفاتورة تلقائيًا)
    # ---------------------------------------------------------------------
    def _get_sale_orders_from_invoice(self):
        self.ensure_one()
        # 1) من invoice_line -> sale_line -> order
        sale_orders = self.invoice_id.invoice_line_ids.mapped("sale_line_ids.order_id")
        sale_orders = sale_orders.filtered(lambda so: so)
        if sale_orders:
            return sale_orders

        # 2) fallback: من invoice_origin
        if self.invoice_id.invoice_origin:
            so = self.env["sale.order"].search([("name", "=", self.invoice_id.invoice_origin)], limit=1)
            if so:
                return so
        return self.env["sale.order"]

    def _get_export_shipments_from_invoice(self):
        self.ensure_one()
        sale_orders = self._get_sale_orders_from_invoice()
        shipments = sale_orders.mapped("export_shipment_id").filtered(lambda s: s)
        return shipments

    def _get_lots_from_invoice_auto(self):
        """العميل مش هيختار lot: احنا هنجيبها تلقائيًا من Export Shipment"""
        self.ensure_one()

        shipments = self._get_export_shipments_from_invoice()
        lots = shipments.mapped("lot_line_ids.lot_id").filtered(lambda l: l)
        return lots

    # ---------------------------------------------------------------------
    # MRP genealogy: finished lot -> raw lots (مع recursion)
    # ---------------------------------------------------------------------
    def _find_productions_producing_lot(self, lot):
        """يرجع الـ MOs اللي خرجت Finished Product بالـ lot ده"""
        Production = self.env["mrp.production"]
        # نجيب move_lines اللي فيها اللوت على finished moves
        # أسلم طريقة: ندور في stock.move.line المرتبط بـ move_finished_ids
        mls = self.env["stock.move.line"].search([
            ("lot_id", "=", lot.id),
            ("move_id.production_id", "!=", False),
            ("move_id.raw_material_production_id", "=", False),  # ده يميّز finished move غالبًا
            ("state", "=", "done"),
        ], limit=200)
        productions = mls.mapped("move_id.production_id").filtered(lambda p: p)
        if not productions:
            # fallback أوسع
            productions = Production.search([
                ("move_finished_ids.move_line_ids.lot_id", "=", lot.id),
                ("state", "in", ["done", "progress", "to_close"]),
            ], limit=50)
        return productions

    def _get_raw_lots_from_production(self, production):
        """يرجع Raw lots اللي دخلت على الـ MO"""
        raw_mls = production.move_raw_ids.mapped("move_line_ids").filtered(lambda ml: ml.lot_id and ml.state == "done")
        return raw_mls.mapped("lot_id")

    def _resolve_source_raw_lots_recursive(self, finished_lots):
        """
        لكل finished lot:
        - هات الـ MOs اللي عملته
        - هات raw lots اللي دخلت
        - لو الـ raw lot نفسه ناتج تصنيع (semi-finished) نكمل recursion
        """
        self.ensure_one()

        result_map = {}  # lot_id -> set(raw_lot_ids)
        visited = set()

        def dfs(lot):
            if not lot:
                return set()
            if lot.id in visited:
                return set()
            visited.add(lot.id)

            productions = self._find_productions_producing_lot(lot)
            if not productions:
                # مش ناتج تصنيع: ده غالبًا خام/شراء/تعديل مخزون
                return set([lot.id])  # نعتبره “مصدر” نهائي

            all_sources = set()
            for prod in productions:
                raw_lots = self._get_raw_lots_from_production(prod)
                if not raw_lots:
                    continue
                for rl in raw_lots:
                    # recursion
                    sub = dfs(rl)
                    if sub:
                        all_sources |= sub
                    else:
                        all_sources.add(rl.id)
            return all_sources

        for fl in finished_lots:
            src_ids = dfs(fl)
            # في حالتنا: المصدر النهائي اللي يهمنا هو خام (المزرعة / المورد)
            # لو رجع finished نفسه كمصدر، هنسيبه
            result_map[fl.id] = src_ids

        return result_map

    # ---------------------------------------------------------------------
    # Build report lines
    # ---------------------------------------------------------------------
    def _collect_move_lines_for_lots(self, lots):
        """نجمع حركة اللوتات (incoming/outgoing/internal/mrp)"""
        self.ensure_one()
        MoveLine = self.env["stock.move.line"]

        mls = MoveLine.search([
            ("lot_id", "in", lots.ids),
            ("state", "=", "done"),
        ], order="date asc, id asc", limit=2000)

        return mls

    def _build_lines(self):
        self.ensure_one()

        # 1) lots من invoice تلقائيًا
        auto_lots = self._get_lots_from_invoice_auto()

        # 2) لو المستخدم اختار lots يدويًا → نفلتر بيها
        lots = auto_lots
        if self.lot_ids:
            lots = auto_lots.filtered(lambda l: l.id in self.lot_ids.ids)

        if not lots:
            raise UserError(_("No lots found for this invoice (via Export Shipment)."))

        # 3) نجيب source raw lots mapping
        src_map = self._resolve_source_raw_lots_recursive(lots)

        # 4) نجمع حركة اللوتات نفسها
        move_lines = self._collect_move_lines_for_lots(lots)

        # 5) نكتب lines
        self.line_ids.unlink()
        lines_vals = []
        for ml in move_lines:
            picking = ml.picking_id
            move = ml.move_id
            production = move.production_id or move.raw_material_production_id

            pick_code = picking.picking_type_id.code if picking and picking.picking_type_id else False
            # تصنيع بيبان غالبًا internal + production_id، فهنسميه mrp_operation لما فيه MO
            if production:
                pick_code = "mrp_operation"

            src_ids = list(src_map.get(ml.lot_id.id, set())) if ml.lot_id else []
            lines_vals.append({
                "wizard_id": self.id,
                "date": ml.date,
                "lot_id": ml.lot_id.id,
                "product_id": ml.product_id.id,
                "qty": ml.qty_done,
                "uom_id": ml.product_uom_id.id,
                "picking_id": picking.id if picking else False,
                "picking_type_code": pick_code,
                "partner_id": picking.partner_id.id if picking and picking.partner_id else False,
                "location_from_id": ml.location_id.id,
                "location_to_id": ml.location_dest_id.id,
                "origin_ref": (picking.origin if picking else False) or (move.reference if hasattr(move, "reference") else False),
                "production_id": production.id if production else False,
                "source_raw_lot_ids": [(6, 0, src_ids)],
            })

        self.env["lot.traceability.line"].create(lines_vals)
        return lots, auto_lots

    # ---------------------------------------------------------------------
    # Buttons
    # ---------------------------------------------------------------------
    def action_generate(self):
        self.ensure_one()
        self._build_lines()
        return {
            "type": "ir.actions.act_window",
            "res_model": "lot.traceability.wizard",
            "res_id": self.id,
            "view_mode": "form",
            "target": "new",
        }

    def action_open_screen(self):
        """يفتح Tree/Form لشاشة النتائج"""
        self.ensure_one()
        self._build_lines()

        action = self.env.ref("export_shipment.action_lot_traceability_lines").read()[0]
        action["domain"] = [("wizard_id", "=", self.id)]
        action["context"] = {"search_default_group_by_lot": 1}
        return action

    def action_download_pdf(self):
        self.ensure_one()
        self._build_lines()
        return self.env.ref("export_shipment.action_report_lot_traceability").report_action(self)

    def action_download_excel(self):
        self.ensure_one()
        # (مفترض إن الاكسيل عندك شغال بالفعل)
        self._build_lines()
        return self.env["ir.actions.report"]._get_report_from_name(
            "export_shipment.lot_traceability_xlsx"
        ).report_action(self)
