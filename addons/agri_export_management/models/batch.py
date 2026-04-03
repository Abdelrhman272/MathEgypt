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
    evaluation_id = fields.Many2one("agx.evaluation")
    incoming_picking_id = fields.Many2one("stock.picking", string="Incoming Receipt", domain="[('picking_type_id.code','=','incoming')]")
    mrp_production_id = fields.Many2one("mrp.production", string="Related Manufacturing Order", copy=False)
    consumption_picking_id = fields.Many2one('stock.picking', string='Consumption Transfer', copy=False, readonly=True)
    output_picking_id = fields.Many2one('stock.picking', string='Output Transfer', copy=False, readonly=True)
    stock_transfer_count = fields.Integer(compute='_compute_transfer_count')
    stock_transfer_state = fields.Char(compute='_compute_transfer_count')
    state = fields.Selection([("draft", "Draft"), ("in_progress", "In Progress"), ("done", "Done"), ("cancelled", "Cancelled")], default="draft", tracking=True)
    input_line_ids = fields.One2many("agx.batch.input", "batch_id", string="Inputs", copy=True)
    output_line_ids = fields.One2many("agx.batch.output", "batch_id", string="Outputs", copy=True)
    input_qty = fields.Float(compute="_compute_qty_totals", store=True)
    output_qty = fields.Float(compute="_compute_qty_totals", store=True)
    variance_qty = fields.Float(compute="_compute_qty_totals", store=True)
    currency_id = fields.Many2one("res.currency", related="company_id.currency_id", store=True, readonly=True)
    costing_status = fields.Selection([("manual", "Manual"), ("actual", "Actual")], compute="_compute_costing_totals", store=True)
    cost_basis_note = fields.Char(compute="_compute_costing_totals")
    manual_raw_material_cost = fields.Monetary(currency_field="currency_id", default=0.0)
    manual_operation_cost = fields.Monetary(currency_field="currency_id", default=0.0)
    manual_other_cost = fields.Monetary(currency_field="currency_id", default=0.0)
    actual_raw_material_cost = fields.Monetary(currency_field="currency_id", default=0.0, readonly=True)
    actual_operation_cost = fields.Monetary(currency_field="currency_id", default=0.0, readonly=True)
    effective_allocable_cost = fields.Monetary(currency_field="currency_id", compute="_compute_costing_totals", store=True)
    total_relative_sales_value = fields.Monetary(currency_field="currency_id", compute="_compute_costing_totals", store=True)
    actual_cost_last_refresh = fields.Datetime(readonly=True)

    @api.depends("consumption_picking_id.state", "output_picking_id.state")
    def _compute_transfer_count(self):
        for rec in self:
            transfers = (rec.consumption_picking_id | rec.output_picking_id).filtered(lambda p: p)
            rec.stock_transfer_count = len(transfers)
            rec.stock_transfer_state = ', '.join(sorted(set(transfers.mapped('state')))) if transfers else False

    @api.depends("input_line_ids.qty", "output_line_ids.qty")
    def _compute_qty_totals(self):
        for rec in self:
            rec.input_qty = sum(rec.input_line_ids.mapped("qty"))
            rec.output_qty = sum(rec.output_line_ids.mapped("qty"))
            rec.variance_qty = rec.input_qty - rec.output_qty

    @api.depends(
        "manual_raw_material_cost", "manual_operation_cost", "manual_other_cost",
        "actual_raw_material_cost", "actual_operation_cost", "output_line_ids.sales_value",
    )
    def _compute_costing_totals(self):
        for rec in self:
            raw_cost = rec.actual_raw_material_cost or rec.manual_raw_material_cost
            op_cost = rec.actual_operation_cost or rec.manual_operation_cost
            other_cost = rec.manual_other_cost
            rec.effective_allocable_cost = raw_cost + op_cost + other_cost
            rec.total_relative_sales_value = sum(rec.output_line_ids.mapped("sales_value"))
            rec.costing_status = "actual" if (rec.actual_raw_material_cost or rec.actual_operation_cost) else "manual"
            if rec.actual_raw_material_cost:
                rec.cost_basis_note = _("Raw material cost uses the linked incoming receipt purchase price when available, otherwise product cost.")
            else:
                rec.cost_basis_note = _("Using manual fallback costs.")

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("name", _("New")) == _("New"):
                vals["name"] = self.env["ir.sequence"].next_by_code("agx.batch") or _("New")
        return super().create(vals_list)

    def _move_qty_field(self):
        Move = self.env['stock.move']
        for field_name in ('product_uom_qty', 'quantity'):
            if field_name in Move._fields:
                return field_name
        return 'product_uom_qty'

    def _move_line_qty_field(self):
        MoveLine = self.env['stock.move.line']
        for field_name in ('quantity', 'qty_done', 'quantity_product_uom'):
            if field_name in MoveLine._fields:
                return field_name
        return 'quantity'

    def _prepare_move_vals(self, product, qty, uom, location_id, location_dest_id):
        move_qty_field = self._move_qty_field()
        vals = {
            'name': product.display_name,
            'product_id': product.id,
            move_qty_field: qty,
            'location_id': location_id,
            'location_dest_id': location_dest_id,
        }
        if 'product_uom' in self.env['stock.move']._fields:
            vals['product_uom'] = uom.id
        return vals

    def _prepare_move_line_vals(self, picking, move, product, qty, lot, location_id, location_dest_id):
        qty_field = self._move_line_qty_field()
        vals = {
            'picking_id': picking.id,
            'move_id': move.id,
            'product_id': product.id,
            'location_id': location_id,
            'location_dest_id': location_dest_id,
        }
        vals[qty_field] = qty
        if lot:
            vals['lot_id'] = lot.id
        if 'product_uom_id' in self.env['stock.move.line']._fields:
            vals['product_uom_id'] = (move.product_uom.id if getattr(move, 'product_uom', False) else product.uom_id.id)
        return vals

    def action_start(self):
        self.write({"state": "in_progress"})

    def action_load_inputs_from_receipt(self):
        for rec in self:
            if not rec.incoming_picking_id:
                raise UserError(_("Please select an incoming receipt first."))
            commands = [(5, 0, 0)]
            qty_field = rec._move_line_qty_field()
            move_lines = rec.incoming_picking_id.move_line_ids.filtered(lambda ml: ml.product_id and ml.state == 'done')
            if move_lines:
                for ml in move_lines:
                    qty = getattr(ml, qty_field, 0.0) or getattr(ml, 'qty_done', 0.0) or 0.0
                    if not qty:
                        continue
                    commands.append((0, 0, {
                        'product_id': ml.product_id.id,
                        'lot_id': ml.lot_id.id if ml.lot_id else False,
                        'qty': qty,
                        'uom_id': ml.product_id.uom_id.id,
                    }))
            else:
                move_qty_field = rec._move_qty_field()
                moves = rec.incoming_picking_id.move_ids_without_package.filtered(lambda m: m.product_id and m.state == 'done')
                for move in moves:
                    qty = getattr(move, move_qty_field, 0.0) or 0.0
                    if not qty:
                        continue
                    commands.append((0, 0, {
                        'product_id': move.product_id.id,
                        'qty': qty,
                        'uom_id': move.product_id.uom_id.id,
                    }))
            rec.write({'input_line_ids': commands})

    def _get_internal_picking_type(self):
        self.ensure_one()
        return self.company_id.agx_internal_picking_type_id or self.env['stock.picking.type'].search([('code', '=', 'internal'), ('company_id', 'in', [self.company_id.id, False])], limit=1)

    def _get_production_location(self):
        self.ensure_one()
        return self.company_id.agx_production_location_id or self.env.ref('stock.stock_location_production', raise_if_not_found=False)

    def _get_finished_location(self):
        self.ensure_one()
        return self.company_id.agx_finished_goods_location_id or (self.incoming_picking_id.location_dest_id if self.incoming_picking_id else False) or self.env.ref('stock.stock_location_stock', raise_if_not_found=False)

    def action_prepare_stock_transfers(self):
        for rec in self:
            picking_type = rec._get_internal_picking_type()
            production_loc = rec._get_production_location()
            finished_loc = rec._get_finished_location()
            source_loc = rec.incoming_picking_id.location_dest_id or finished_loc
            if not picking_type or not production_loc or not finished_loc or not source_loc:
                raise UserError(_("Please configure internal picking type, production location, and finished goods/source locations in Settings before preparing stock transfers."))
            if not rec.consumption_picking_id and rec.input_line_ids:
                consumption = self.env['stock.picking'].create({
                    'picking_type_id': picking_type.id,
                    'location_id': source_loc.id,
                    'location_dest_id': production_loc.id,
                    'origin': rec.name,
                    'company_id': rec.company_id.id,
                    'agx_batch_id': rec.id,
                    'agx_flow_type': 'batch_input',
                    'move_ids_without_package': [(0, 0, rec._prepare_move_vals(line.product_id, line.qty, line.uom_id or line.product_id.uom_id, source_loc.id, production_loc.id)) for line in rec.input_line_ids if line.product_id and line.qty],
                })
                consumption.action_confirm()
                rec.consumption_picking_id = consumption.id
            if not rec.output_picking_id and rec.output_line_ids:
                output = self.env['stock.picking'].create({
                    'picking_type_id': picking_type.id,
                    'location_id': production_loc.id,
                    'location_dest_id': finished_loc.id,
                    'origin': rec.name,
                    'company_id': rec.company_id.id,
                    'agx_batch_id': rec.id,
                    'agx_flow_type': 'batch_output',
                    'move_ids_without_package': [(0, 0, rec._prepare_move_vals(line.product_id, line.qty, line.uom_id or line.product_id.uom_id, production_loc.id, finished_loc.id)) for line in rec.output_line_ids if line.product_id and line.qty],
                })
                output.action_confirm()
                rec.output_picking_id = output.id
        return True

    def action_validate_stock_transfers(self):
        MoveLine = self.env['stock.move.line']
        for rec in self:
            if not rec.consumption_picking_id or not rec.output_picking_id:
                rec.action_prepare_stock_transfers()
            for picking, lines, source_loc, dest_loc in [
                (rec.consumption_picking_id, rec.input_line_ids, rec.consumption_picking_id.location_id, rec.consumption_picking_id.location_dest_id),
                (rec.output_picking_id, rec.output_line_ids, rec.output_picking_id.location_id, rec.output_picking_id.location_dest_id),
            ]:
                if not picking:
                    continue
                if picking.state == 'draft':
                    picking.action_confirm()
                if picking.state not in ('done', 'cancel') and hasattr(picking, 'action_assign'):
                    picking.action_assign()
                existing = picking.move_line_ids
                if existing:
                    existing.unlink()
                for line in lines:
                    if not line.product_id or not line.qty:
                        continue
                    move = picking.move_ids_without_package.filtered(lambda m: m.product_id == line.product_id)[:1]
                    if not move:
                        continue
                    lot = getattr(line, 'lot_id', False)
                    MoveLine.create(rec._prepare_move_line_vals(picking, move, line.product_id, line.qty, lot, source_loc.id, dest_loc.id))
                if picking.state not in ('done', 'cancel') and hasattr(picking, 'button_validate'):
                    picking.button_validate()
        return True

    def action_view_stock_transfers(self):
        self.ensure_one()
        transfers = (self.consumption_picking_id | self.output_picking_id).filtered(lambda p: p)
        action = {
            'type': 'ir.actions.act_window',
            'name': _('Stock Transfers'),
            'res_model': 'stock.picking',
            'view_mode': 'list,form',
            'domain': [('id', 'in', transfers.ids)],
            'target': 'current',
        }
        if len(transfers) == 1:
            action.update({'view_mode': 'form', 'res_id': transfers.id, 'domain': []})
        return action

    def action_done(self):
        for rec in self:
            if rec.company_id.agx_auto_generate_lot_numbers:
                for line in rec.output_line_ids.filtered(lambda l: not l.lot_id):
                    lot = self.env['stock.lot'].create({
                        'name': self.env['ir.sequence'].next_by_code('stock.lot.serial') or f"{rec.name}-{line.product_id.default_code or line.product_id.id}",
                        'product_id': line.product_id.id,
                        'company_id': rec.company_id.id,
                    })
                    line.lot_id = lot.id
            if rec.incoming_picking_id and not rec.input_line_ids:
                rec.action_load_inputs_from_receipt()
            rec.action_prepare_stock_transfers()
            rec.action_validate_stock_transfers()
            rec.action_refresh_actual_costs()
            rec.write({"state": "done"})

    def action_cancel(self):
        self.write({"state": "cancelled"})

    def action_refresh_actual_costs(self):
        for rec in self:
            raw_cost = 0.0
            for line in rec.input_line_ids:
                unit_cost = line.product_id.standard_price or 0.0
                if rec.incoming_picking_id:
                    moves = rec.incoming_picking_id.move_ids_without_package.filtered(lambda m: m.product_id == line.product_id and m.state == 'done')
                    purchase_moves = moves.filtered(lambda m: getattr(m, 'purchase_line_id', False))
                    total_move_qty = sum(getattr(m, rec._move_qty_field(), 0.0) or 0.0 for m in purchase_moves) or 0.0
                    total_move_value = sum((m.purchase_line_id.price_unit or 0.0) * (getattr(m, rec._move_qty_field(), 0.0) or 0.0) for m in purchase_moves)
                    if total_move_qty:
                        unit_cost = total_move_value / total_move_qty
                raw_cost += (line.qty or 0.0) * unit_cost
            operation_cost = 0.0
            rec.write({
                "actual_raw_material_cost": raw_cost,
                "actual_operation_cost": operation_cost,
                "actual_cost_last_refresh": fields.Datetime.now(),
            })


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

    @api.onchange('product_id')
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

    @api.onchange('product_id')
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
