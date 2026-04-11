from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError


class AgxShipment(models.Model):
    _name = "agx.shipment"
    _description = "Export Shipment"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "shipment_date desc, id desc"

    name = fields.Char(default=lambda self: _("New"), copy=False, readonly=True)
    shipment_date = fields.Date(default=fields.Date.context_today, tracking=True)
    company_id = fields.Many2one("res.company", required=True, default=lambda self: self.env.company, index=True)
    currency_id = fields.Many2one("res.currency", related="company_id.currency_id", store=True, readonly=True)
    customer_id = fields.Many2one("res.partner", string="Customer", tracking=True)
    destination_id = fields.Many2one("agx.destination", tracking=True)
    container_no = fields.Char(tracking=True)
    container_count = fields.Integer(default=1, tracking=True)
    seal_no = fields.Char()
    state = fields.Selection([
        ("draft", "Draft"),
        ("reserved", "Reserved"),
        ("shipped", "Shipped"),
        ("cancelled", "Cancelled"),
    ], default="draft", tracking=True)
    container_ids = fields.One2many("agx.shipment.container", "shipment_id", string="Containers", copy=True)
    line_ids = fields.One2many("agx.shipment.line", "shipment_id", string="Products", copy=True)
    lot_line_ids = fields.One2many("agx.shipment.lot.line", "shipment_id", string="Reserved Lots", copy=True)
    cost_line_ids = fields.One2many("agx.shipment.cost.line", "shipment_id", string="Logistics Costs", copy=True)
    sale_order_id = fields.Many2one("sale.order", copy=False)
    delivery_picking_id = fields.Many2one("stock.picking", copy=False, readonly=True)
    total_qty = fields.Float(compute="_compute_profitability", store=True)
    total_reserved_qty = fields.Float(compute="_compute_profitability", store=True)
    total_logistics_cost = fields.Monetary(currency_field="currency_id", compute="_compute_profitability", store=True)
    cost_per_container = fields.Monetary(currency_field="currency_id", compute="_compute_profitability", store=True)
    revenue_amount = fields.Monetary(currency_field="currency_id", compute="_compute_profitability", store=True)
    gross_profit_amount = fields.Monetary(currency_field="currency_id", compute="_compute_profitability", store=True)
    gross_margin_pct = fields.Float(compute="_compute_profitability", store=True, digits=(16, 2))
    note = fields.Html()

    @api.depends(
        "line_ids.product_qty",
        "lot_line_ids.reserved_qty",
        "cost_line_ids.effective_amount",
        "sale_order_id.amount_untaxed",
        "container_count",
    )
    def _compute_profitability(self):
        for rec in self:
            rec.total_qty = sum(rec.line_ids.mapped("product_qty"))
            rec.total_reserved_qty = sum(rec.lot_line_ids.mapped("reserved_qty"))
            rec.total_logistics_cost = sum(rec.cost_line_ids.mapped("effective_amount"))
            effective_container_count = rec.container_count or len(rec.container_ids) or 1
            rec.cost_per_container = rec.total_logistics_cost / effective_container_count if effective_container_count else 0.0
            rec.revenue_amount = rec.sale_order_id.amount_untaxed if rec.sale_order_id else 0.0
            rec.gross_profit_amount = rec.revenue_amount - rec.total_logistics_cost
            rec.gross_margin_pct = (rec.gross_profit_amount / rec.revenue_amount * 100.0) if rec.revenue_amount else 0.0

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("name", _("New")) == _("New"):
                vals["name"] = self.env["ir.sequence"].next_by_code("agx.shipment") or _("New")
        records = super().create(vals_list)
        records._sync_container_setup()
        return records

    def write(self, vals):
        res = super().write(vals)
        if not self.env.context.get("agx_skip_container_sync"):
            self._sync_container_setup()
        return res

    def _sync_container_setup(self):
        Container = self.env["agx.shipment.container"]
        for rec in self:
            target_count = rec.container_count or 1
            if not rec.container_ids:
                for index in range(target_count):
                    Container.create({
                        "shipment_id": rec.id,
                        "sequence": (index + 1) * 10,
                        "name": f"{rec.name}-C{index + 1}",
                        "container_no": rec.container_no if index == 0 else False,
                        "seal_no": rec.seal_no if index == 0 else False,
                    })
            elif len(rec.container_ids) < target_count:
                existing = len(rec.container_ids)
                for index in range(existing, target_count):
                    Container.create({
                        "shipment_id": rec.id,
                        "sequence": (index + 1) * 10,
                        "name": f"{rec.name}-C{index + 1}",
                    })
            if rec.container_ids:
                first = rec.container_ids.sorted("sequence")[:1]
                if first:
                    first = first[0]
                    update_vals = {}
                    if rec.container_no and first.container_no != rec.container_no:
                        update_vals["container_no"] = rec.container_no
                    if rec.seal_no and first.seal_no != rec.seal_no:
                        update_vals["seal_no"] = rec.seal_no
                    if update_vals:
                        first.write(update_vals)
                    header_vals = {}
                    if not rec.container_no and first.container_no:
                        header_vals["container_no"] = first.container_no
                    if not rec.seal_no and first.seal_no:
                        header_vals["seal_no"] = first.seal_no
                    if rec.container_count != len(rec.container_ids):
                        header_vals["container_count"] = len(rec.container_ids)
                    if header_vals:
                        rec.with_context(agx_skip_container_sync=True).write(header_vals)

    def _quantity_field_name(self, model):
        return "quantity" if "quantity" in model._fields else "qty_done"

    def _lock_reservation_scope(self, candidate_quant_ids, lot_pairs):
        self.ensure_one()
        candidate_quant_ids = tuple(sorted(set(candidate_quant_ids or [])))
        lot_pairs = sorted(set(lot_pairs or []))
        self.env["stock.quant"].flush_model(["company_id", "product_id", "lot_id", "quantity", "reserved_quantity", "location_id"])
        self.env["agx.shipment.lot.line"].flush_model(["shipment_id", "product_id", "lot_id", "reserved_qty"])
        self.env.cr.execute("SELECT id FROM agx_shipment WHERE id = %s FOR UPDATE", (self.id,))
        if candidate_quant_ids:
            self.env.cr.execute(
                """
                SELECT sq.id
                  FROM stock_quant sq
                 WHERE sq.id IN %s
                 ORDER BY sq.id
                 FOR UPDATE
                """,
                (candidate_quant_ids,),
            )
        if lot_pairs:
            values_sql = ",".join(["(%s,%s)"] * len(lot_pairs))
            pair_params = []
            for product_id, lot_id in lot_pairs:
                pair_params.extend([product_id, lot_id])
            self.env.cr.execute(
                f"""
                SELECT ll.id
                  FROM agx_shipment_lot_line ll
                  JOIN agx_shipment s ON s.id = ll.shipment_id
                  JOIN (VALUES {values_sql}) AS scope(product_id, lot_id)
                    ON scope.product_id = ll.product_id
                   AND scope.lot_id = ll.lot_id
                 WHERE s.company_id = %s
                   AND s.state != 'cancelled'
                 ORDER BY ll.product_id, ll.lot_id, ll.id
                 FOR UPDATE
                """,
                tuple(pair_params + [self.company_id.id]),
            )

    def _get_candidate_reservation_scope(self):
        self.ensure_one()
        Quant = self.env["stock.quant"]
        BatchOutput = self.env["agx.batch.output"]
        finished_location = self.company_id.agx_finished_goods_location_id
        if not finished_location:
            raise UserError(_("Please configure Finished Goods Location in Settings before reserving lots."))
        candidate_quant_ids = []
        candidate_pairs = set()
        pending_reserved = {}
        for line in self.line_ids:
            needed = line.product_qty
            if needed <= 0:
                continue
            quants = Quant.search([
                ("company_id", "=", self.company_id.id),
                ("product_id", "=", line.product_id.id),
                ("location_id.usage", "=", "internal"),
                ("location_id", "child_of", finished_location.id),
                ("lot_id", "!=", False),
                ("quantity", ">", 0),
            ], order="in_date,id")
            for quant in quants:
                pair = (line.product_id.id, quant.lot_id.id)
                effective_available = self._get_lot_effective_available_qty(
                    lot_id=quant.lot_id.id,
                    product_id=line.product_id.id,
                    exclude_shipment_id=self.id,
                ) - pending_reserved.get(pair, 0.0)
                effective_available = max(effective_available, 0.0)
                if effective_available <= 0:
                    continue
                output = BatchOutput.search([
                    ("lot_id", "=", quant.lot_id.id),
                    ("product_id", "=", line.product_id.id),
                ], limit=1)
                if line.grade_id and output and output.grade_id != line.grade_id:
                    continue
                if line.size_id and output and output.size_id != line.size_id:
                    continue
                take = min(needed, effective_available)
                if take <= 0:
                    continue
                candidate_quant_ids.append(quant.id)
                candidate_pairs.add(pair)
                pending_reserved[pair] = pending_reserved.get(pair, 0.0) + take
                needed -= take
                if needed <= 0:
                    break
            if needed > 0:
                raise UserError(_("Not enough available lots for product %s.") % line.product_id.display_name)
        return sorted(set(candidate_quant_ids)), sorted(candidate_pairs)

    def _compute_reservation_allocations_from_candidates(self, candidate_quant_ids):
        self.ensure_one()
        BatchOutput = self.env["agx.batch.output"]
        allocations = []
        pending_reserved = {}
        candidate_quants = self.env["stock.quant"].search([("id", "in", candidate_quant_ids)], order="in_date,id")
        quants_by_product = {}
        for quant in candidate_quants:
            quants_by_product.setdefault(quant.product_id.id, []).append(quant)
        for line in self.line_ids:
            needed = line.product_qty
            if needed <= 0:
                continue
            quants = quants_by_product.get(line.product_id.id, [])
            for quant in quants:
                pair = (line.product_id.id, quant.lot_id.id)
                effective_available = self._get_lot_effective_available_qty(
                    lot_id=quant.lot_id.id,
                    product_id=line.product_id.id,
                    exclude_shipment_id=self.id,
                ) - pending_reserved.get(pair, 0.0)
                effective_available = max(effective_available, 0.0)
                if effective_available <= 0:
                    continue
                output = BatchOutput.search([
                    ("lot_id", "=", quant.lot_id.id),
                    ("product_id", "=", line.product_id.id),
                ], limit=1)
                if line.grade_id and output and output.grade_id != line.grade_id:
                    continue
                if line.size_id and output and output.size_id != line.size_id:
                    continue
                take = min(needed, effective_available)
                if take <= 0:
                    continue
                allocations.append((0, 0, {
                    "shipment_line_id": line.id,
                    "product_id": line.product_id.id,
                    "lot_id": quant.lot_id.id,
                    "batch_output_id": output.id if output else False,
                    "available_qty": effective_available,
                    "reserved_qty": take,
                }))
                pending_reserved[pair] = pending_reserved.get(pair, 0.0) + take
                needed -= take
                if needed <= 0:
                    break
            if needed > 0:
                raise UserError(_("Not enough available lots for product %s.") % line.product_id.display_name)
        return allocations

    def _lock_lot_reservation_scope(self, lot_id, product_id):
        self.ensure_one()
        if not lot_id or not product_id:
            return
        self.env["stock.quant"].flush_model(["company_id", "product_id", "lot_id", "quantity", "reserved_quantity", "location_id"])
        self.env["agx.shipment.lot.line"].flush_model(["shipment_id", "product_id", "lot_id", "reserved_qty"])
        self.env.cr.execute(
            """
            SELECT sq.id
              FROM stock_quant sq
              JOIN stock_location sl ON sl.id = sq.location_id
             WHERE sq.company_id = %s
               AND sq.product_id = %s
               AND sq.lot_id = %s
               AND sl.usage = 'internal'
             ORDER BY sq.id
             FOR UPDATE
            """,
            (self.company_id.id, product_id, lot_id),
        )
        self.env.cr.execute(
            """
            SELECT ll.id
              FROM agx_shipment_lot_line ll
              JOIN agx_shipment s ON s.id = ll.shipment_id
             WHERE s.company_id = %s
               AND ll.product_id = %s
               AND ll.lot_id = %s
               AND s.state != 'cancelled'
             ORDER BY ll.id
             FOR UPDATE
            """,
            (self.company_id.id, product_id, lot_id),
        )

    def _get_other_reserved_qty(self, lot_id, product_id, exclude_shipment_id=False, exclude_lot_line_id=False):
        domain = [
            ("lot_id", "=", lot_id),
            ("product_id", "=", product_id),
            ("shipment_id.state", "in", ["draft", "reserved"]),
        ]
        if exclude_shipment_id:
            domain.append(("shipment_id", "!=", exclude_shipment_id))
        if exclude_lot_line_id:
            domain.append(("id", "!=", exclude_lot_line_id))
        return sum(self.env["agx.shipment.lot.line"].search(domain).mapped("reserved_qty"))

    def _get_lot_physical_available_qty(self, lot_id, product_id):
        finished_location = self.company_id.agx_finished_goods_location_id
        if not finished_location:
            return 0.0
        quants = self.env["stock.quant"].search([
            ("company_id", "=", self.company_id.id),
            ("product_id", "=", product_id),
            ("lot_id", "=", lot_id),
            ("location_id.usage", "=", "internal"),
            ("location_id", "child_of", finished_location.id),
        ])
        available_qty = 0.0
        for quant in quants:
            available_qty += max((quant.quantity or 0.0) - (quant.reserved_quantity or 0.0), 0.0)
        return available_qty

    def _get_lot_effective_available_qty(self, lot_id, product_id, exclude_shipment_id=False, exclude_lot_line_id=False):
        physical_available = self._get_lot_physical_available_qty(lot_id, product_id)
        other_reserved = self._get_other_reserved_qty(
            lot_id=lot_id,
            product_id=product_id,
            exclude_shipment_id=exclude_shipment_id,
            exclude_lot_line_id=exclude_lot_line_id,
        )
        return max(physical_available - other_reserved, 0.0)

    def _normalize_line_containers(self):
        for rec in self:
            fallback_container = rec.container_ids.sorted("sequence")[:1]
            fallback_container = fallback_container[0] if fallback_container else False
            if fallback_container:
                for line in rec.line_ids.filtered(lambda l: not l.container_id):
                    line.container_id = fallback_container.id

    def _get_default_stock_location(self):
        self.ensure_one()
        return self.env["stock.location"].search([
            ("company_id", "in", [False, self.company_id.id]),
            ("usage", "=", "internal"),
        ], limit=1)

    def _get_finished_goods_location(self):
        self.ensure_one()
        return self.company_id.agx_finished_goods_location_id

    def _get_customer_location(self):
        self.ensure_one()
        return self.env["stock.location"].search([
            ("usage", "=", "customer"),
            ("company_id", "in", [False, self.company_id.id]),
        ], limit=1)

    def _get_outgoing_picking_type(self):
        self.ensure_one()
        return self.company_id.agx_outgoing_picking_type_id

    def _validate_delivery_settings(self):
        self.ensure_one()
        source_location = self._get_finished_goods_location()
        customer_location = self._get_customer_location()
        outgoing_type = self._get_outgoing_picking_type()
        if not source_location or not customer_location or not outgoing_type:
            raise UserError(_("Please configure Finished Goods Location and Outgoing Delivery Type in Settings."))
        return source_location, customer_location, outgoing_type

    def _cancel_existing_delivery(self):
        for rec in self:
            picking = rec.delivery_picking_id
            if picking and picking.state not in ("done", "cancel"):
                moves = picking.move_ids.filtered(lambda m: m.state not in ("done", "cancel"))
                if moves:
                    moves._action_cancel()
                picking.message_post(body=_("Delivery rebuilt from latest AGX reservation."))

    def _build_delivery_picking(self):
        Move = self.env["stock.move"]
        for rec in self:
            source_location, customer_location, outgoing_type = rec._validate_delivery_settings()
            rec._cancel_existing_delivery()
            picking = self.env["stock.picking"].create({
                "picking_type_id": outgoing_type.id,
                "location_id": source_location.id,
                "location_dest_id": customer_location.id,
                "partner_id": rec.customer_id.id if rec.customer_id else False,
                "origin": rec.name,
                "company_id": rec.company_id.id,
                "agx_shipment_id": rec.id,
            })
            for line in rec.line_ids.filtered(lambda l: l.product_id and l.product_qty > 0):
                Move.create({
                    "name": f"{rec.name} / Delivery / {line.product_id.display_name}",
                    "description_picking": f"{rec.name} / Delivery / {line.product_id.display_name}",
                    "company_id": rec.company_id.id,
                    "product_id": line.product_id.id,
                    "product_uom_qty": line.product_qty,
                    "product_uom": (line.uom_id or line.product_id.uom_id).id,
                    "location_id": source_location.id,
                    "location_dest_id": customer_location.id,
                    "agx_shipment_id": rec.id,
                    "agx_shipment_line_id": line.id,
                    "agx_flow_type": "shipment",
                    "origin": rec.name,
                    "picking_id": picking.id,
                })
            picking.action_confirm()
            picking.action_assign()
            rec.delivery_picking_id = picking.id

    def action_reserve(self):
        for rec in self:
            rec._validate_delivery_settings()
            rec._sync_container_setup()
            rec._normalize_line_containers()
            candidate_quant_ids, candidate_pairs = rec._get_candidate_reservation_scope()
            rec._lock_reservation_scope(candidate_quant_ids, candidate_pairs)
            allocations = rec._compute_reservation_allocations_from_candidates(candidate_quant_ids)
            rec.lot_line_ids.unlink()
            if allocations:
                rec.write({"lot_line_ids": allocations})
            rec._build_delivery_picking()
            rec.state = "reserved"
        return True

    def action_ship(self):
        MoveLine = self.env["stock.move.line"]
        for rec in self:
            if not rec.lot_line_ids:
                raise UserError(_("Please reserve lots before shipping."))
            dangling_lot_lines = rec.lot_line_ids.filtered(lambda l: not l.shipment_line_id)
            if dangling_lot_lines:
                raise UserError(_("All reserved lots must be linked to shipment product lines before shipping."))
            for line in rec.line_ids.filtered(lambda l: l.product_id and l.product_qty > 0):
                reserved_qty = sum(rec.lot_line_ids.filtered(lambda l: l.shipment_line_id == line).mapped("reserved_qty"))
                if reserved_qty < line.product_qty:
                    raise UserError(_("Reserved quantity for product %(product)s is insufficient. Required: %(required)s, Reserved: %(reserved)s") % {
                        "product": line.product_id.display_name,
                        "required": line.product_qty,
                        "reserved": reserved_qty,
                    })
            if not rec.delivery_picking_id or rec.delivery_picking_id.state in ("cancel",):
                rec._build_delivery_picking()
            picking = rec.delivery_picking_id
            picking.action_assign()
            quantity_field = rec._quantity_field_name(MoveLine)
            source_location = picking.location_id
            dest_location = picking.location_dest_id
            for move in picking.move_ids.filtered(lambda m: m.state not in ("done", "cancel")):
                move_lines = move.move_line_ids
                move_lines.unlink()
                reserved_lines = rec.lot_line_ids.filtered(lambda l: l.shipment_line_id and l.shipment_line_id == move.agx_shipment_line_id)
                if not reserved_lines:
                    raise UserError(_("Missing reserved lots for delivery move %s.") % move.display_name)
                move.product_uom_qty = sum(reserved_lines.mapped("reserved_qty"))
                for lot_line in reserved_lines:
                    vals = {
                        "move_id": move.id,
                        "product_id": move.product_id.id,
                        "product_uom_id": move.product_uom.id,
                        "location_id": source_location.id,
                        "location_dest_id": dest_location.id,
                        "lot_id": lot_line.lot_id.id,
                    }
                    vals[quantity_field] = lot_line.reserved_qty
                    MoveLine.create(vals)
            res = picking.with_context(skip_immediate=True, skip_backorder=True).button_validate()
            if isinstance(res, dict):
                pending_moves = picking.move_ids.filtered(lambda m: m.state not in ("done", "cancel"))
                if pending_moves:
                    pending_moves._action_done()
            rec.state = "shipped"
        return True

    def action_cancel(self):
        self.write({"state": "cancelled"})

    def action_create_sale_order(self):
        self.ensure_one()
        if not self.customer_id:
            raise UserError(_("Please select a customer before creating the Sales Order."))
        if self.sale_order_id:
            return self.action_view_sale_order()
        product = self.company_id.agx_container_service_product_id or self.env["product.product"].search([("sale_ok", "=", True)], limit=1)
        if not product:
            raise UserError(_("Please configure a container service product in Settings."))
        so = self.env["sale.order"].create({
            "partner_id": self.customer_id.id,
            "company_id": self.company_id.id,
            "origin": self.name,
            "order_line": [(0, 0, {
                "product_id": product.id,
                "name": f"{self.company_id.agx_so_line_prefix or 'Shipment'} {self.name}",
                "product_uom_qty": self.container_count or len(self.container_ids) or 1,
                "price_unit": 0.0,
            })],
        })
        self.sale_order_id = so.id
        return self.action_view_sale_order()

    def action_view_sale_order(self):
        self.ensure_one()
        if not self.sale_order_id:
            return False
        return {
            "type": "ir.actions.act_window",
            "name": _("Sales Order"),
            "res_model": "sale.order",
            "res_id": self.sale_order_id.id,
            "view_mode": "form",
            "target": "current",
        }

    def action_view_delivery(self):
        self.ensure_one()
        if not self.delivery_picking_id:
            return False
        return {
            "type": "ir.actions.act_window",
            "name": _("Delivery"),
            "res_model": "stock.picking",
            "res_id": self.delivery_picking_id.id,
            "view_mode": "form",
            "target": "current",
        }


class AgxShipmentContainer(models.Model):
    _name = "agx.shipment.container"
    _description = "Shipment Container"
    _order = "sequence, id"

    sequence = fields.Integer(default=10)
    shipment_id = fields.Many2one("agx.shipment", required=True, ondelete="cascade")
    name = fields.Char()
    container_no = fields.Char()
    seal_no = fields.Char()
    note = fields.Char()
    line_ids = fields.One2many("agx.shipment.line", "container_id", string="Container Product Lines")

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        for rec in records:
            if not rec.name:
                rec.name = f"{rec.shipment_id.name}-C{len(rec.shipment_id.container_ids)}"
            if rec.shipment_id and rec.shipment_id.container_count != len(rec.shipment_id.container_ids):
                rec.shipment_id.with_context(agx_skip_container_sync=True).write({"container_count": len(rec.shipment_id.container_ids)})
        return records

    def write(self, vals):
        res = super().write(vals)
        for rec in self:
            first_container = rec.shipment_id.container_ids.sorted("sequence")[:1] if rec.shipment_id else False
            if first_container and rec.id == first_container.id:
                header_vals = {
                    "container_no": rec.container_no,
                    "seal_no": rec.seal_no,
                }
                rec.shipment_id.with_context(agx_skip_container_sync=True).write(header_vals)
        return res


class AgxShipmentLine(models.Model):
    _name = "agx.shipment.line"
    _description = "Shipment Product Line"
    _order = "sequence, id"

    sequence = fields.Integer(default=10)
    shipment_id = fields.Many2one("agx.shipment", required=True, ondelete="cascade")
    container_id = fields.Many2one("agx.shipment.container", string="Container")
    company_id = fields.Many2one(related="shipment_id.company_id", store=True, readonly=True)
    currency_id = fields.Many2one(related="shipment_id.currency_id", store=True, readonly=True)
    product_id = fields.Many2one("product.product", required=True)
    grade_id = fields.Many2one("agx.grade")
    size_id = fields.Many2one("agx.size")
    product_qty = fields.Float(required=True, default=1.0)
    uom_id = fields.Many2one("uom.uom")
    carton_qty = fields.Float(default=0.0)
    net_weight = fields.Float(default=0.0)
    gross_weight = fields.Float(default=0.0)
    allocated_logistics_cost = fields.Monetary(currency_field="currency_id", compute="_compute_allocated_costs", store=True)
    cost_per_carton = fields.Monetary(currency_field="currency_id", compute="_compute_allocated_costs", store=True)
    cost_per_qty = fields.Monetary(currency_field="currency_id", compute="_compute_allocated_costs", store=True)

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        for rec in records:
            if not rec.container_id and rec.shipment_id.container_ids:
                first_container = rec.shipment_id.container_ids.sorted("sequence")[:1]
                if first_container:
                    rec.container_id = first_container.id
        return records

    @api.onchange("product_id")
    def _onchange_product_id(self):
        for rec in self:
            if rec.product_id:
                rec.uom_id = rec.product_id.uom_id
            if not rec.container_id and rec.shipment_id.container_ids:
                rec.container_id = rec.shipment_id.container_ids.sorted("sequence")[:1]

    @api.constrains("container_id", "shipment_id")
    def _check_container_shipment(self):
        for rec in self:
            if rec.container_id and rec.container_id.shipment_id != rec.shipment_id:
                raise ValidationError(_("The selected container must belong to the same shipment."))

    @api.depends(
        "shipment_id.cost_line_ids.effective_amount",
        "shipment_id.cost_line_ids.allocation_basis",
        "shipment_id.line_ids.product_qty",
        "shipment_id.line_ids.carton_qty",
        "shipment_id.line_ids.net_weight",
        "shipment_id.line_ids.gross_weight",
    )
    def _compute_allocated_costs(self):
        for line in self:
            total_allocated = 0.0
            shipment = line.shipment_id
            lines = shipment.line_ids
            for cost_line in shipment.cost_line_ids:
                if cost_line.allocation_basis == "carton":
                    denominator = sum(lines.mapped("carton_qty")); numerator = line.carton_qty
                elif cost_line.allocation_basis == "net_weight":
                    denominator = sum(lines.mapped("net_weight")); numerator = line.net_weight
                elif cost_line.allocation_basis == "gross_weight":
                    denominator = sum(lines.mapped("gross_weight")); numerator = line.gross_weight
                elif cost_line.allocation_basis == "equal":
                    denominator = len(lines); numerator = 1.0 if line.id else 0.0
                else:
                    denominator = sum(lines.mapped("product_qty")); numerator = line.product_qty
                if denominator:
                    total_allocated += cost_line.effective_amount * (numerator / denominator)
            line.allocated_logistics_cost = total_allocated
            line.cost_per_carton = total_allocated / line.carton_qty if line.carton_qty else 0.0
            line.cost_per_qty = total_allocated / line.product_qty if line.product_qty else 0.0


class AgxShipmentLotLine(models.Model):
    _name = "agx.shipment.lot.line"
    _description = "Reserved Lot Line"
    _order = "sequence, id"

    sequence = fields.Integer(default=10)
    shipment_id = fields.Many2one("agx.shipment", required=True, ondelete="cascade")
    shipment_line_id = fields.Many2one("agx.shipment.line")
    container_id = fields.Many2one("agx.shipment.container", related="shipment_line_id.container_id", store=True, readonly=True)
    product_id = fields.Many2one("product.product", required=True)
    lot_id = fields.Many2one("stock.lot", required=True)
    batch_output_id = fields.Many2one("agx.batch.output")
    available_qty = fields.Float()
    reserved_qty = fields.Float(required=True, default=1.0)
    note = fields.Char()

    @api.constrains("reserved_qty", "available_qty", "lot_id", "product_id", "shipment_id")
    def _check_reserved_qty(self):
        for rec in self:
            if rec.reserved_qty <= 0:
                raise ValidationError(_("Reserved quantity must be greater than zero."))
            if rec.shipment_id:
                rec.shipment_id._lock_lot_reservation_scope(lot_id=rec.lot_id.id, product_id=rec.product_id.id)
            effective_available = rec.shipment_id._get_lot_effective_available_qty(
                lot_id=rec.lot_id.id,
                product_id=rec.product_id.id,
                exclude_lot_line_id=rec.id,
            )
            if rec.reserved_qty > effective_available:
                raise ValidationError(_("Reserved quantity cannot exceed the currently available quantity for this lot."))


class AgxShipmentCostLine(models.Model):
    _name = "agx.shipment.cost.line"
    _description = "Shipment Cost Line"
    _order = "sequence, id"

    sequence = fields.Integer(default=10)
    shipment_id = fields.Many2one("agx.shipment", required=True, ondelete="cascade")
    company_id = fields.Many2one(related="shipment_id.company_id", store=True, readonly=True)
    currency_id = fields.Many2one(related="shipment_id.currency_id", store=True, readonly=True)
    name = fields.Char()
    cost_type_id = fields.Many2one("agx.shipment.cost.type", required=True)
    allocation_basis = fields.Selection([
        ("qty", "By Quantity"),
        ("carton", "By Cartons"),
        ("net_weight", "By Net Weight"),
        ("gross_weight", "By Gross Weight"),
        ("equal", "Equal Share"),
    ], default="qty", required=True)
    cost_source = fields.Selection([
        ("manual", "Manual"),
        ("vendor_bill", "Vendor Bill"),
        ("vendor_bill_line", "Vendor Bill Line"),
        ("landed_cost", "Landed Cost"),
    ], default="manual", required=True)
    manual_amount = fields.Monetary(currency_field="currency_id", default=0.0)
    source_amount = fields.Monetary(currency_field="currency_id", compute="_compute_source_amount", store=True)
    effective_amount = fields.Monetary(currency_field="currency_id", compute="_compute_source_amount", store=True)
    vendor_bill_id = fields.Many2one("account.move", domain="[('move_type', '=', 'in_invoice')]")
    vendor_bill_line_id = fields.Many2one("account.move.line")
    landed_cost_id = fields.Many2one("stock.landed.cost")
    note = fields.Char()

    @api.depends("cost_source", "manual_amount", "vendor_bill_id.amount_untaxed", "vendor_bill_line_id.price_subtotal", "landed_cost_id.amount_total")
    def _compute_source_amount(self):
        for rec in self:
            source_amount = 0.0
            if rec.cost_source == "vendor_bill_line" and rec.vendor_bill_line_id:
                source_amount = rec.vendor_bill_line_id.price_subtotal
            elif rec.cost_source == "vendor_bill" and rec.vendor_bill_id:
                source_amount = rec.vendor_bill_id.amount_untaxed
            elif rec.cost_source == "landed_cost" and rec.landed_cost_id:
                source_amount = rec.landed_cost_id.amount_total
            rec.source_amount = source_amount
            rec.effective_amount = source_amount if rec.cost_source != "manual" else rec.manual_amount
