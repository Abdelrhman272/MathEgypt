from collections import defaultdict

from odoo import _, api, fields, models
from odoo.exceptions import UserError


class AgxBatch(models.Model):
    _name = "agx.batch"
    _description = "Production Batch"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "batch_date desc, id desc"

    name = fields.Char(default=lambda self: _("New"), copy=False, readonly=True)
    batch_date = fields.Date(default=fields.Date.context_today, tracking=True)
    company_id = fields.Many2one("res.company", required=True, default=lambda self: self.env.company, index=True)
    evaluation_id = fields.Many2one("agx.evaluation", tracking=True)
    state = fields.Selection([
        ("draft", "Draft"),
        ("in_progress", "In Progress"),
        ("done", "Done"),
        ("cancelled", "Cancelled"),
    ], default="draft", tracking=True)
    input_line_ids = fields.One2many("agx.batch.input", "batch_id", string="Inputs", copy=True)
    output_line_ids = fields.One2many("agx.batch.output", "batch_id", string="Outputs", copy=True)
    input_qty = fields.Float(compute="_compute_qty_totals", store=True)
    output_qty = fields.Float(compute="_compute_qty_totals", store=True)
    variance_qty = fields.Float(compute="_compute_qty_totals", store=True)
    currency_id = fields.Many2one("res.currency", related="company_id.currency_id", store=True, readonly=True)

    manual_raw_material_cost = fields.Monetary(currency_field="currency_id", default=0.0)
    manual_operation_cost = fields.Monetary(currency_field="currency_id", default=0.0)
    manual_other_cost = fields.Monetary(currency_field="currency_id", default=0.0)
    actual_raw_material_cost = fields.Monetary(currency_field="currency_id", compute="_compute_actual_costs", store=True)
    actual_operation_cost = fields.Monetary(currency_field="currency_id", compute="_compute_actual_costs", store=True)
    effective_allocable_cost = fields.Monetary(currency_field="currency_id", compute="_compute_costing", store=True)
    total_relative_sales_value = fields.Monetary(currency_field="currency_id", compute="_compute_costing", store=True)
    costing_status = fields.Selection([("manual", "Manual"), ("actual", "Actual")], compute="_compute_costing", store=True)
    cost_basis_note = fields.Char(compute="_compute_costing", store=True)

    stock_move_ids = fields.One2many("stock.move", "agx_batch_id", string="Stock Moves", readonly=True)
    stock_picking_ids = fields.One2many("stock.picking", "agx_batch_id", string="Stock Transfers", readonly=True)
    stock_move_count = fields.Integer(compute="_compute_stock_counts")
    stock_move_state = fields.Selection([
        ("none", "No Transfers"),
        ("partial", "Partial"),
        ("done", "Done"),
    ], compute="_compute_stock_counts")
    receipt_count = fields.Integer(compute="_compute_stock_counts")

    @api.depends("input_line_ids.qty", "output_line_ids.qty")
    def _compute_qty_totals(self):
        for rec in self:
            rec.input_qty = sum(rec.input_line_ids.mapped("qty"))
            rec.output_qty = sum(rec.output_line_ids.mapped("qty"))
            rec.variance_qty = rec.output_qty - rec.input_qty

    @api.depends("evaluation_id", "stock_picking_ids.state", "stock_move_ids.state")
    def _compute_stock_counts(self):
        Picking = self.env["stock.picking"]
        for rec in self:
            pickings = rec.stock_picking_ids
            rec.stock_move_count = len(pickings)
            done_count = len(pickings.filtered(lambda p: p.state == "done"))
            if not pickings:
                rec.stock_move_state = "none"
            elif done_count == len(pickings):
                rec.stock_move_state = "done"
            else:
                rec.stock_move_state = "partial"
            rec.receipt_count = Picking.search_count([
                ("agx_evaluation_id", "=", rec.evaluation_id.id),
                ("picking_type_id.code", "=", "incoming"),
                ("state", "=", "done"),
            ]) if rec.evaluation_id else 0

    @api.depends("evaluation_id", "input_line_ids.qty", "input_line_ids.product_id", "input_line_ids.lot_id")
    def _compute_actual_costs(self):
        for rec in self:
            raw_cost = 0.0
            if rec.evaluation_id:
                pickings = rec._get_done_receipts()
                for line in rec.input_line_ids:
                    remaining = line.qty or 0.0
                    if not remaining:
                        continue
                    moves = pickings.mapped("move_ids").filtered(lambda m: m.product_id == line.product_id and m.state == "done")
                    for move in moves:
                        move_qty = move.quantity if hasattr(move, "quantity") else move.product_uom_qty
                        if line.lot_id and move.move_line_ids:
                            ml_qty = sum(move.move_line_ids.filtered(lambda ml: ml.lot_id == line.lot_id).mapped("quantity")) if 'quantity' in move.move_line_ids._fields else sum(move.move_line_ids.filtered(lambda ml: ml.lot_id == line.lot_id).mapped("qty_done"))
                            move_qty = ml_qty or move_qty
                        if move_qty <= 0:
                            continue
                        take_qty = min(remaining, move_qty)
                        price_unit = move.purchase_line_id.price_unit if move.purchase_line_id else line.product_id.standard_price
                        raw_cost += take_qty * price_unit
                        remaining -= take_qty
                        if remaining <= 0:
                            break
                    if remaining > 0:
                        raw_cost += remaining * (line.product_id.standard_price or 0.0)
            else:
                raw_cost = sum((line.qty or 0.0) * (line.product_id.standard_price or 0.0) for line in rec.input_line_ids)
            rec.actual_raw_material_cost = raw_cost
            rec.actual_operation_cost = 0.0

    @api.depends(
        "manual_raw_material_cost", "manual_operation_cost", "manual_other_cost",
        "actual_raw_material_cost", "actual_operation_cost", "output_line_ids.sales_value",
    )
    def _compute_costing(self):
        for rec in self:
            use_actual = bool(rec.actual_raw_material_cost or rec.actual_operation_cost)
            raw_cost = rec.actual_raw_material_cost if use_actual else rec.manual_raw_material_cost
            op_cost = rec.actual_operation_cost if use_actual else rec.manual_operation_cost
            rec.effective_allocable_cost = raw_cost + op_cost + rec.manual_other_cost
            rec.total_relative_sales_value = sum(rec.output_line_ids.mapped("sales_value"))
            rec.costing_status = "actual" if use_actual else "manual"
            if use_actual and rec.evaluation_id:
                rec.cost_basis_note = _("Based on done incoming receipts linked to the selected evaluation, with fallback to product cost when needed.")
            else:
                rec.cost_basis_note = _("Based on manual fallback costs.")

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("name", _("New")) == _("New"):
                vals["name"] = self.env["ir.sequence"].next_by_code("agx.batch") or _("New")
        return super().create(vals_list)

    @api.onchange("evaluation_id")
    def _onchange_evaluation_id(self):
        for rec in self:
            rec._load_inputs_from_evaluation_receipts(onchange_mode=True)

    def _get_done_receipts(self):
        self.ensure_one()
        if not self.evaluation_id:
            return self.env["stock.picking"]
        return self.env["stock.picking"].search([
            ("agx_evaluation_id", "=", self.evaluation_id.id),
            ("picking_type_id.code", "=", "incoming"),
            ("state", "=", "done"),
            ("company_id", "=", self.company_id.id),
        ])

    def _load_inputs_from_evaluation_receipts(self, onchange_mode=False):
        for rec in self:
            if not rec.evaluation_id:
                continue
            pickings = rec._get_done_receipts()
            if not pickings:
                if onchange_mode:
                    rec.input_line_ids = [(5, 0, 0)]
                continue
            grouped = defaultdict(lambda: {"qty": 0.0, "product_id": False, "lot_id": False, "uom_id": False})
            for picking in pickings:
                if picking.move_line_ids:
                    for ml in picking.move_line_ids.filtered(lambda l: l.product_id and (getattr(l, 'quantity', 0.0) or getattr(l, 'qty_done', 0.0))):
                        qty = ml.quantity if 'quantity' in ml._fields else ml.qty_done
                        key = (ml.product_id.id, ml.lot_id.id if ml.lot_id else False, ml.product_uom_id.id if ml.product_uom_id else ml.product_id.uom_id.id)
                        grouped[key]["qty"] += qty
                        grouped[key]["product_id"] = ml.product_id.id
                        grouped[key]["lot_id"] = ml.lot_id.id if ml.lot_id else False
                        grouped[key]["uom_id"] = ml.product_uom_id.id if ml.product_uom_id else ml.product_id.uom_id.id
                else:
                    for move in picking.move_ids.filtered(lambda m: m.product_id and m.state == "done"):
                        qty = move.quantity if hasattr(move, "quantity") else move.product_uom_qty
                        key = (move.product_id.id, False, move.product_uom.id if move.product_uom else move.product_id.uom_id.id)
                        grouped[key]["qty"] += qty
                        grouped[key]["product_id"] = move.product_id.id
                        grouped[key]["lot_id"] = False
                        grouped[key]["uom_id"] = move.product_uom.id if move.product_uom else move.product_id.uom_id.id
            commands = [(5, 0, 0)]
            for vals in grouped.values():
                commands.append((0, 0, vals))
            rec.input_line_ids = commands

    def action_start(self):
        self.write({"state": "in_progress"})

    def action_done(self):
        for rec in self:
            if not rec.input_line_ids:
                rec._load_inputs_from_evaluation_receipts(onchange_mode=False)
            if not rec.input_line_ids:
                raise UserError(_("Please load or add at least one input line before marking the batch as done."))
            if not rec.output_line_ids:
                raise UserError(_("Please add at least one output line before marking the batch as done."))
            rec._ensure_output_lots()
            rec._validate_stock_posting()
            rec._post_stock_pickings()
            rec.state = "done"
        return True

    def action_cancel(self):
        self.write({"state": "cancelled"})

    def _ensure_output_lots(self):
        for rec in self:
            if not rec.company_id.agx_auto_generate_lot_numbers:
                continue
            for line in rec.output_line_ids.filtered(lambda l: not l.lot_id and l.product_id):
                lot_name = self.env["ir.sequence"].next_by_code("stock.lot.serial") or f"{rec.name}-{line.product_id.default_code or line.product_id.id}"
                line.lot_id = self.env["stock.lot"].create({
                    "name": lot_name,
                    "product_id": line.product_id.id,
                    "company_id": rec.company_id.id,
                }).id

    def _get_default_stock_location(self):
        self.ensure_one()
        return self.env["stock.location"].search([
            ("company_id", "in", [False, self.company_id.id]),
            ("usage", "=", "internal"),
        ], limit=1)

    def _get_default_production_location(self):
        self.ensure_one()
        return self.env["stock.location"].search([
            ("company_id", "in", [False, self.company_id.id]),
            ("usage", "=", "production"),
        ], limit=1) or self._get_default_stock_location()

    def _get_internal_picking_type(self):
        self.ensure_one()
        return self.company_id.agx_internal_picking_type_id or self.env["stock.picking.type"].search([
            ("code", "=", "internal"),
            ("company_id", "in", [False, self.company_id.id]),
        ], limit=1, order="company_id desc,id")

    def _quantity_field_name(self, model):
        return "quantity" if "quantity" in model._fields else "qty_done"

    def _get_line_available_qty(self, line):
        self.ensure_one()
        domain = [
            ("company_id", "=", self.company_id.id),
            ("product_id", "=", line.product_id.id),
            ("location_id.usage", "=", "internal"),
        ]
        if line.lot_id:
            domain.append(("lot_id", "=", line.lot_id.id))
        quants = self.env["stock.quant"].search(domain)
        available_qty = 0.0
        for quant in quants:
            available_qty += max((quant.quantity or 0.0) - (quant.reserved_quantity or 0.0), 0.0)
        return available_qty

    def _validate_stock_posting(self):
        self.ensure_one()
        production_location = self.company_id.agx_production_location_id or self._get_default_production_location()
        finished_location = self.company_id.agx_finished_goods_location_id or self._get_default_stock_location()
        internal_type = self._get_internal_picking_type()
        if not production_location or not finished_location or not internal_type:
            raise UserError(_("Please configure Production Location, Finished Goods Location, and Internal Transfer Type in Settings."))
        if self.stock_picking_ids.filtered(lambda p: p.state == "done"):
            raise UserError(_("This batch already has posted stock transfers. To avoid duplicate inventory movements, a done batch cannot be posted again."))
        for line in self.input_line_ids.filtered(lambda l: l.product_id and l.qty > 0):
            available_qty = self._get_line_available_qty(line)
            if available_qty < line.qty:
                if line.lot_id:
                    raise UserError(_("Not enough available quantity for product %(product)s in lot %(lot)s. Needed: %(needed)s, Available: %(available)s") % {
                        "product": line.product_id.display_name,
                        "lot": line.lot_id.display_name,
                        "needed": line.qty,
                        "available": available_qty,
                    })
                raise UserError(_("Not enough available quantity for product %(product)s. Needed: %(needed)s, Available: %(available)s") % {
                    "product": line.product_id.display_name,
                    "needed": line.qty,
                    "available": available_qty,
                })

    def _prepare_move_line_vals(self, move, product, uom, location_id, location_dest_id, qty, lot_id=False):
        vals = {
            "move_id": move.id,
            "product_id": product.id,
            "product_uom_id": uom.id,
            "location_id": location_id.id,
            "location_dest_id": location_dest_id.id,
        }
        vals[self._quantity_field_name(self.env["stock.move.line"])] = qty
        if lot_id:
            vals["lot_id"] = lot_id.id
        return vals

    def _input_source_location(self, line):
        self.ensure_one()
        quant = False
        if line.lot_id:
            quant = self.env["stock.quant"].search([
                ("product_id", "=", line.product_id.id),
                ("lot_id", "=", line.lot_id.id),
                ("company_id", "=", self.company_id.id),
                ("location_id.usage", "=", "internal"),
                ("quantity", ">", 0),
            ], limit=1)
        if quant:
            return quant.location_id
        receipts = self._get_done_receipts()
        dests = receipts.mapped("location_dest_id")
        return dests[:1] if dests else (self.company_id.agx_raw_material_location_id or self._get_default_stock_location())

    def _validate_picking(self, picking):
        res = picking.with_context(skip_immediate=True, skip_backorder=True).button_validate()
        if isinstance(res, dict):
            pending_moves = picking.move_ids.filtered(lambda m: m.state not in ("done", "cancel"))
            if pending_moves:
                pending_moves._action_done()
        return True

    def _create_picking(self, picking_type, location_id, location_dest_id, origin):
        return self.env["stock.picking"].create({
            "picking_type_id": picking_type.id,
            "location_id": location_id.id,
            "location_dest_id": location_dest_id.id,
            "origin": origin,
            "company_id": self.company_id.id,
            "agx_batch_id": self.id,
        })

    def _post_stock_pickings(self):
        Move = self.env["stock.move"]
        MoveLine = self.env["stock.move.line"]
        all_created_moves = self.env["stock.move"]
        for rec in self:
            production_location = rec.company_id.agx_production_location_id or rec._get_default_production_location()
            finished_location = rec.company_id.agx_finished_goods_location_id or rec._get_default_stock_location()
            internal_type = rec._get_internal_picking_type()
            if not internal_type:
                raise UserError(_("Please configure an internal transfer type in Settings."))

            input_groups = defaultdict(list)
            for line in rec.input_line_ids.filtered(lambda l: l.product_id and l.qty > 0):
                source_location = rec._input_source_location(line)
                input_groups[source_location.id].append((source_location, line))

            for source_location_id, grouped_lines in input_groups.items():
                source_location = grouped_lines[0][0]
                picking = rec._create_picking(
                    picking_type=internal_type,
                    location_id=source_location,
                    location_dest_id=production_location,
                    origin=f"{rec.name} / Consumption",
                )
                created_input_pairs = []
                for _source_location, line in grouped_lines:
                    move = Move.create({
                        "name": f"{rec.name} / Consume / {line.product_id.display_name}",
                        "description_picking": f"{rec.name} / Consume / {line.product_id.display_name}",
                        "company_id": rec.company_id.id,
                        "product_id": line.product_id.id,
                        "product_uom_qty": line.qty,
                        "product_uom": (line.uom_id or line.product_id.uom_id).id,
                        "location_id": source_location.id,
                        "location_dest_id": production_location.id,
                        "agx_batch_id": rec.id,
                        "agx_flow_type": "consume",
                        "origin": rec.name,
                        "picking_id": picking.id,
                    })
                    all_created_moves |= move
                    created_input_pairs.append((move, line))
                picking.action_confirm()
                for move, line in created_input_pairs:
                    move.move_line_ids.unlink()
                    MoveLine.create(rec._prepare_move_line_vals(
                        move=move,
                        product=line.product_id,
                        uom=(line.uom_id or line.product_id.uom_id),
                        location_id=source_location,
                        location_dest_id=production_location,
                        qty=line.qty,
                        lot_id=line.lot_id,
                    ))
                rec._validate_picking(picking)

            output_picking = rec._create_picking(
                picking_type=internal_type,
                location_id=production_location,
                location_dest_id=finished_location,
                origin=f"{rec.name} / Output",
            )
            created_output_pairs = []
            for line in rec.output_line_ids.filtered(lambda l: l.product_id and l.qty > 0):
                move = Move.create({
                    "name": f"{rec.name} / Output / {line.product_id.display_name}",
                    "description_picking": f"{rec.name} / Output / {line.product_id.display_name}",
                    "company_id": rec.company_id.id,
                    "product_id": line.product_id.id,
                    "product_uom_qty": line.qty,
                    "product_uom": (line.uom_id or line.product_id.uom_id).id,
                    "location_id": production_location.id,
                    "location_dest_id": finished_location.id,
                    "agx_batch_id": rec.id,
                    "agx_flow_type": "output",
                    "origin": rec.name,
                    "picking_id": output_picking.id,
                })
                all_created_moves |= move
                created_output_pairs.append((move, line))
            output_picking.action_confirm()
            for move, line in created_output_pairs:
                move.move_line_ids.unlink()
                MoveLine.create(rec._prepare_move_line_vals(
                    move=move,
                    product=line.product_id,
                    uom=(line.uom_id or line.product_id.uom_id),
                    location_id=production_location,
                    location_dest_id=finished_location,
                    qty=line.qty,
                    lot_id=line.lot_id,
                ))
            rec._validate_picking(output_picking)
        return all_created_moves

    def action_view_stock_moves(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Stock Transfers"),
            "res_model": "stock.picking",
            "view_mode": "list,form",
            "domain": [("agx_batch_id", "=", self.id)],
            "target": "current",
        }

    def action_view_receipts(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Incoming Receipts"),
            "res_model": "stock.picking",
            "view_mode": "list,form",
            "domain": [("agx_evaluation_id", "=", self.evaluation_id.id), ("picking_type_id.code", "=", "incoming")],
            "target": "current",
        }


class AgxBatchInput(models.Model):
    _name = "agx.batch.input"
    _description = "Batch Input"
    _order = "sequence, id"

    sequence = fields.Integer(default=10)
    batch_id = fields.Many2one("agx.batch", required=True, ondelete="cascade")
    product_id = fields.Many2one("product.product", required=True)
    lot_id = fields.Many2one("stock.lot")
    qty = fields.Float(required=True, default=1.0)
    uom_id = fields.Many2one("uom.uom")
    note = fields.Char()

    @api.onchange("product_id")
    def _onchange_product_id(self):
        for rec in self:
            if rec.product_id:
                rec.uom_id = rec.product_id.uom_id


class AgxBatchOutput(models.Model):
    _name = "agx.batch.output"
    _description = "Batch Output"
    _order = "sequence, id"

    sequence = fields.Integer(default=10)
    batch_id = fields.Many2one("agx.batch", required=True, ondelete="cascade")
    company_id = fields.Many2one(related="batch_id.company_id", store=True, readonly=True)
    currency_id = fields.Many2one(related="batch_id.currency_id", store=True, readonly=True)
    product_id = fields.Many2one("product.product", required=True)
    lot_id = fields.Many2one("stock.lot", string="Output Lot")
    grade_id = fields.Many2one("agx.grade")
    size_id = fields.Many2one("agx.size")
    qty = fields.Float(required=True, default=1.0)
    uom_id = fields.Many2one("uom.uom")
    sales_price_unit = fields.Monetary(currency_field="currency_id", default=0.0)
    sales_value = fields.Monetary(currency_field="currency_id", compute="_compute_cost_share", store=True)
    relative_sales_ratio = fields.Float(compute="_compute_cost_share", store=True, digits=(16, 6))
    allocated_cost = fields.Monetary(currency_field="currency_id", compute="_compute_cost_share", store=True)
    cost_per_unit = fields.Monetary(currency_field="currency_id", compute="_compute_cost_share", store=True)

    @api.onchange("product_id")
    def _onchange_product_id(self):
        for rec in self:
            if rec.product_id:
                rec.uom_id = rec.product_id.uom_id

    @api.depends("qty", "sales_price_unit", "batch_id.effective_allocable_cost", "batch_id.total_relative_sales_value")
    def _compute_cost_share(self):
        for rec in self:
            rec.sales_value = (rec.qty or 0.0) * (rec.sales_price_unit or 0.0)
            total_sales = rec.batch_id.total_relative_sales_value or 0.0
            rec.relative_sales_ratio = (rec.sales_value / total_sales) if total_sales else 0.0
            rec.allocated_cost = (rec.batch_id.effective_allocable_cost or 0.0) * rec.relative_sales_ratio
            rec.cost_per_unit = (rec.allocated_cost / rec.qty) if rec.qty else 0.0
