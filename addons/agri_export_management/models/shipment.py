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
    state = fields.Selection([("draft", "Draft"), ("reserved", "Reserved"), ("shipped", "Shipped"), ("cancelled", "Cancelled")], default="draft", tracking=True)
    line_ids = fields.One2many("agx.shipment.line", "shipment_id", string="Products", copy=True)
    lot_line_ids = fields.One2many("agx.shipment.lot.line", "shipment_id", string="Reserved Lots", copy=True)
    cost_line_ids = fields.One2many("agx.shipment.cost.line", "shipment_id", string="Logistics Costs", copy=True)
    sale_order_id = fields.Many2one("sale.order", copy=False)
    delivery_picking_id = fields.Many2one('stock.picking', copy=False, readonly=True)
    invoice_count = fields.Integer(compute='_compute_commercial_counts')
    delivery_count = fields.Integer(compute='_compute_commercial_counts')
    total_qty = fields.Float(compute="_compute_profitability", store=True)
    total_reserved_qty = fields.Float(compute="_compute_profitability", store=True)
    total_logistics_cost = fields.Monetary(currency_field="currency_id", compute="_compute_profitability", store=True)
    manual_logistics_cost = fields.Monetary(currency_field='currency_id', compute='_compute_profitability', store=True)
    actual_logistics_cost = fields.Monetary(currency_field='currency_id', compute='_compute_profitability', store=True)
    costing_status = fields.Selection([('manual','Manual'),('partial_actual','Partial Actual'),('full_actual','Full Actual')], compute='_compute_profitability', store=True)
    cost_per_container = fields.Monetary(currency_field="currency_id", compute="_compute_profitability", store=True)
    revenue_amount = fields.Monetary(currency_field="currency_id", compute="_compute_profitability", store=True)
    gross_profit_amount = fields.Monetary(currency_field="currency_id", compute="_compute_profitability", store=True)
    gross_margin_pct = fields.Float(compute="_compute_profitability", store=True, digits=(16, 2))
    note = fields.Html()

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

    def _get_outgoing_picking_type(self):
        self.ensure_one()
        return self.company_id.agx_outgoing_picking_type_id or self.env['stock.picking.type'].search([('code', '=', 'outgoing'), ('company_id', 'in', [self.company_id.id, False])], limit=1)

    def _get_shipment_source_location(self):
        self.ensure_one()
        return self.company_id.agx_shipment_source_location_id or self.company_id.agx_finished_goods_location_id or self.env.ref('stock.stock_location_stock', raise_if_not_found=False)

    def _get_customer_location(self):
        self.ensure_one()
        customer_loc = False
        if self.customer_id and hasattr(self.customer_id, 'property_stock_customer'):
            customer_loc = self.customer_id.property_stock_customer
        if not customer_loc:
            outgoing = self._get_outgoing_picking_type()
            customer_loc = outgoing.default_location_dest_id if outgoing else False
        return customer_loc or self.env.ref('stock.stock_location_customers', raise_if_not_found=False)

    def _compute_commercial_counts(self):
        for rec in self:
            so = rec.sale_order_id
            delivery_set = (rec.delivery_picking_id | (so.picking_ids if so else self.env['stock.picking'])).filtered(lambda p: p)
            rec.invoice_count = len(so.invoice_ids) if so else 0
            rec.delivery_count = len(delivery_set)

    @api.depends("line_ids.product_qty", "lot_line_ids.reserved_qty", "cost_line_ids.effective_amount", "cost_line_ids.cost_source", "cost_line_ids.manual_amount", "sale_order_id.amount_untaxed", "container_count")
    def _compute_profitability(self):
        for rec in self:
            rec.total_qty = sum(rec.line_ids.mapped("product_qty"))
            rec.total_reserved_qty = sum(rec.lot_line_ids.mapped("reserved_qty"))
            rec.total_logistics_cost = sum(rec.cost_line_ids.mapped("effective_amount"))
            rec.manual_logistics_cost = sum(rec.cost_line_ids.filtered(lambda l: l.cost_source == 'manual').mapped('effective_amount'))
            rec.actual_logistics_cost = sum(rec.cost_line_ids.filtered(lambda l: l.cost_source != 'manual').mapped('effective_amount'))
            if rec.cost_line_ids and all(l.cost_source != 'manual' for l in rec.cost_line_ids):
                rec.costing_status = 'full_actual'
            elif rec.actual_logistics_cost:
                rec.costing_status = 'partial_actual'
            else:
                rec.costing_status = 'manual'
            rec.cost_per_container = rec.total_logistics_cost / rec.container_count if rec.container_count else 0.0
            rec.revenue_amount = rec.sale_order_id.amount_untaxed if rec.sale_order_id else 0.0
            rec.gross_profit_amount = rec.revenue_amount - rec.total_logistics_cost
            rec.gross_margin_pct = (rec.gross_profit_amount / rec.revenue_amount * 100.0) if rec.revenue_amount else 0.0

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("name", _("New")) == _("New"):
                vals["name"] = self.env["ir.sequence"].next_by_code("agx.shipment") or _("New")
        return super().create(vals_list)

    def action_reserve(self):
        self.write({"state": "reserved"})

    def action_confirm_sale_order(self):
        for rec in self.filtered('sale_order_id'):
            if rec.sale_order_id.state in ('draft', 'sent'):
                rec.sale_order_id.action_confirm()

    def action_create_customer_invoice(self):
        self.ensure_one()
        if not self.sale_order_id:
            raise UserError(_("Please create the Sales Order first."))
        if self.sale_order_id.state in ('draft', 'sent'):
            self.sale_order_id.action_confirm()
        invoices = self.sale_order_id._create_invoices()
        invoices.write({'agx_shipment_id': self.id})
        return self.action_view_invoices()

    def action_view_invoices(self):
        self.ensure_one()
        invoices = self.sale_order_id.invoice_ids if self.sale_order_id else self.env['account.move']
        action = {
            'type': 'ir.actions.act_window',
            'name': _('Customer Invoices'),
            'res_model': 'account.move',
            'view_mode': 'list,form',
            'domain': [('id', 'in', invoices.ids)],
            'target': 'current',
        }
        if len(invoices) == 1:
            action.update({'view_mode': 'form', 'res_id': invoices.id, 'domain': []})
        return action

    def action_prepare_delivery(self):
        for rec in self:
            if rec.delivery_picking_id:
                continue
            if not rec.lot_line_ids:
                raise UserError(_("Please reserve lots before preparing the delivery."))
            picking_type = rec._get_outgoing_picking_type()
            source_loc = rec._get_shipment_source_location()
            dest_loc = rec._get_customer_location()
            if not picking_type or not source_loc or not dest_loc:
                raise UserError(_("Please configure shipment outgoing picking type and shipment source location in Settings."))
            delivery = self.env['stock.picking'].create({
                'picking_type_id': picking_type.id,
                'location_id': source_loc.id,
                'location_dest_id': dest_loc.id,
                'origin': rec.name,
                'partner_id': rec.customer_id.id,
                'company_id': rec.company_id.id,
                'agx_shipment_id': rec.id,
                'agx_flow_type': 'shipment_delivery',
                'move_ids_without_package': [(0, 0, rec._prepare_move_vals(line.product_id, line.product_qty, line.uom_id or line.product_id.uom_id, source_loc.id, dest_loc.id)) for line in rec.line_ids if line.product_id and line.product_qty],
            })
            delivery.action_confirm()
            if hasattr(delivery, 'action_assign'):
                delivery.action_assign()
            move_line_model = self.env['stock.move.line']
            if delivery.move_line_ids:
                delivery.move_line_ids.unlink()
            for lot_line in rec.lot_line_ids:
                move = delivery.move_ids_without_package.filtered(lambda m: m.product_id == lot_line.product_id)[:1]
                if not move:
                    continue
                move_line_model.create(rec._prepare_move_line_vals(delivery, move, lot_line.product_id, lot_line.reserved_qty, lot_line.lot_id, source_loc.id, dest_loc.id))
            rec.delivery_picking_id = delivery.id
        return True

    def action_validate_delivery(self):
        for rec in self:
            if not rec.delivery_picking_id:
                rec.action_prepare_delivery()
            picking = rec.delivery_picking_id
            if picking.state == 'draft':
                picking.action_confirm()
            if picking.state not in ('done', 'cancel') and hasattr(picking, 'action_assign'):
                picking.action_assign()
            if not picking.move_line_ids:
                rec.action_prepare_delivery()
                picking = rec.delivery_picking_id
            if picking.state not in ('done', 'cancel') and hasattr(picking, 'button_validate'):
                picking.button_validate()
            if picking.state == 'done':
                rec.state = 'shipped'
        return True

    def action_run_full_flow(self):
        for rec in self:
            if rec.state == 'draft' and rec.lot_line_ids:
                rec.action_reserve()
            if not rec.sale_order_id:
                rec.action_create_sale_order()
            rec.action_confirm_sale_order()
            rec.action_prepare_delivery()
            rec.action_validate_delivery()
            rec.action_create_customer_invoice()
        return True

    def action_view_deliveries(self):
        self.ensure_one()
        pickings = (self.delivery_picking_id | (self.sale_order_id.picking_ids if self.sale_order_id else self.env['stock.picking'])).filtered(lambda p: p)
        action = {
            'type': 'ir.actions.act_window',
            'name': _('Deliveries'),
            'res_model': 'stock.picking',
            'view_mode': 'list,form',
            'domain': [('id', 'in', pickings.ids)],
            'target': 'current',
        }
        if len(pickings) == 1:
            action.update({'view_mode': 'form', 'res_id': pickings.id, 'domain': []})
        return action

    def action_ship(self):
        for rec in self:
            if rec.sale_order_id and rec.sale_order_id.state in ('draft', 'sent'):
                rec.sale_order_id.action_confirm()
            if rec.lot_line_ids and not rec.delivery_picking_id:
                rec.action_prepare_delivery()
            if rec.delivery_picking_id and rec.delivery_picking_id.state != 'done':
                rec.action_validate_delivery()
            rec.state = 'shipped'

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
                "product_uom_qty": self.container_count or 1,
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


class AgxShipmentLine(models.Model):
    _name = "agx.shipment.line"
    _description = "Shipment Product Line"
    _order = "sequence, id"

    sequence = fields.Integer(default=10)
    shipment_id = fields.Many2one("agx.shipment", required=True, ondelete="cascade")
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

    @api.onchange('product_id')
    def _onchange_product_id(self):
        for rec in self:
            if rec.product_id:
                rec.uom_id = rec.product_id.uom_id

    @api.depends("shipment_id.cost_line_ids.effective_amount", "shipment_id.cost_line_ids.allocation_basis", "shipment_id.line_ids.product_qty", "shipment_id.line_ids.carton_qty", "shipment_id.line_ids.net_weight", "shipment_id.line_ids.gross_weight")
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
    product_id = fields.Many2one("product.product", required=True)
    lot_id = fields.Many2one("stock.lot", required=True)
    batch_output_id = fields.Many2one("agx.batch.output")
    available_qty = fields.Float()
    reserved_qty = fields.Float(required=True, default=1.0)
    note = fields.Char()

    @api.constrains("reserved_qty", "available_qty")
    def _check_reserved_qty(self):
        for rec in self:
            if rec.available_qty and rec.reserved_qty > rec.available_qty:
                raise ValidationError(_("Reserved quantity cannot exceed available quantity."))


class AgxShipmentCostLine(models.Model):
    _name = "agx.shipment.cost.line"
    _description = "Shipment Cost Line"
    _order = "sequence, id"

    sequence = fields.Integer(default=10)
    shipment_id = fields.Many2one("agx.shipment", required=True, ondelete="cascade")
    company_id = fields.Many2one(related="shipment_id.company_id", store=True, readonly=True)
    currency_id = fields.Many2one(related="shipment_id.currency_id", store=True, readonly=True)
    name = fields.Char(required=True)
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
