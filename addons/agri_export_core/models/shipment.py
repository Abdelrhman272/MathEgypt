from odoo import models, fields, api, _
from odoo.exceptions import UserError


class AgriExportShipment(models.Model):
    _name = 'agri.export.shipment'
    _description = 'Export Shipment'

    name = fields.Char(default=lambda self: self.env['ir.sequence'].next_by_code('agri.export.shipment'), readonly=True)

    evaluation_id = fields.Many2one('agri.farm.evaluation')
    container_no = fields.Char(required=True)

    customer_id = fields.Many2one('res.partner', required=True)

    # Container service line (sell by container)
    container_product_id = fields.Many2one('product.product', required=True)
    container_qty = fields.Float(default=1.0)

    sale_order_id = fields.Many2one('sale.order', readonly=True)
    invoice_id = fields.Many2one('account.move', readonly=True)

    picking_id = fields.Many2one('stock.picking', readonly=True)

    reservation_line_ids = fields.One2many('agri.shipment.reservation', 'shipment_id')
    state = fields.Selection([('draft', 'Draft'), ('reserved', 'Reserved'), ('shipped', 'Shipped'), ('invoiced', 'Invoiced')], default='draft')

    def action_fetch_output_lots(self):
        """Populate reservation lines from done production outputs of the linked evaluation."""
        self.ensure_one()
        if not self.evaluation_id:
            raise UserError(_("Please set Farm Evaluation first."))

        # Collect lots from production batches linked to the evaluation
        lines = []
        for batch in self.evaluation_id.production_ids:
            for out in batch.output_line_ids:
                if out.lot_id and out.qty > 0:
                    lines.append((0, 0, {
                        'lot_id': out.lot_id.id,
                        'qty': out.qty,
                    }))

        if not lines:
            raise UserError(_("No production output lots found for this evaluation."))

        # Replace current reservation lines
        self.reservation_line_ids = [(5, 0, 0)] + lines
        return True

    def action_create_sale_order(self):
        self.ensure_one()
        if self.sale_order_id:
            return self.action_open_sale()

        if not self.customer_id:
            raise UserError(_("Please set Customer."))
        if not self.container_product_id:
            raise UserError(_("Please set Container Product (Service)."))
        if self.container_qty <= 0:
            raise UserError(_("Container Qty must be > 0."))

        so = self.env['sale.order'].create({
            'partner_id': self.customer_id.id,
            'origin': self.name,
        })
        self.env['sale.order.line'].create({
            'order_id': so.id,
            'product_id': self.container_product_id.id,
            'name': self.container_product_id.display_name,
            'product_uom_qty': self.container_qty,
            'product_uom': self.container_product_id.uom_id.id,
            'price_unit': 0.0,  # keep generic; user sets price
        })

        self.sale_order_id = so.id
        return {
            'type': 'ir.actions.act_window',
            'name': _('Sale Order'),
            'res_model': 'sale.order',
            'res_id': so.id,
            'view_mode': 'form',
            'target': 'current',
        }

    def action_open_sale(self):
        self.ensure_one()
        if not self.sale_order_id:
            raise UserError(_("No Sale Order linked yet."))
        return {
            'type': 'ir.actions.act_window',
            'name': _('Sale Order'),
            'res_model': 'sale.order',
            'res_id': self.sale_order_id.id,
            'view_mode': 'form',
            'target': 'current',
        }

    def action_create_delivery(self):
        """Create an outgoing picking reserved strictly by lots in reservation lines."""
        self.ensure_one()
        if self.picking_id:
            return self.action_open_picking()

        if not self.reservation_line_ids:
            raise UserError(_("Add at least one reservation line (Lot + Qty)."))

        # Outgoing picking type
        picking_type = self.env['stock.picking.type'].search([('code', '=', 'outgoing')], limit=1)
        if not picking_type:
            raise UserError(_("No outgoing picking type found."))

        customer_loc = self.env.ref('stock.stock_location_customers')
        source_loc = picking_type.default_location_src_id or self.env.ref('stock.stock_location_stock')

        picking = self.env['stock.picking'].create({
            'picking_type_id': picking_type.id,
            'location_id': source_loc.id,
            'location_dest_id': customer_loc.id,
            'partner_id': self.customer_id.id,
            'origin': self.name,
        })

        for line in self.reservation_line_ids:
            if line.qty <= 0:
                continue
            move = self.env['stock.move'].create({
                'name': line.product_id.display_name,
                'product_id': line.product_id.id,
                'product_uom': line.product_id.uom_id.id,
                'product_uom_qty': line.qty,
                'location_id': source_loc.id,
                'location_dest_id': customer_loc.id,
                'picking_id': picking.id,
            })
            # Create move line with lot (strict reservation anchor)
            self.env['stock.move.line'].create({
                'move_id': move.id,
                'picking_id': picking.id,
                'product_id': line.product_id.id,
                'product_uom_id': line.product_id.uom_id.id,
                'location_id': source_loc.id,
                'location_dest_id': customer_loc.id,
                'lot_id': line.lot_id.id,
                'qty_done': 0.0,
            })

        picking.action_confirm()
        picking.action_assign()

        self.picking_id = picking.id
        self.state = 'reserved'

        return self.action_open_picking()

    def action_validate_delivery(self):
        """Auto-fill qty_done from reserved reservation lines then validate picking."""
        self.ensure_one()
        if not self.picking_id:
            raise UserError(_("No Delivery Picking linked yet."))

        picking = self.picking_id
        if picking.state not in ('assigned', 'confirmed'):
            # allow validating even if already done/cancelled; Odoo will raise
            pass

        # Set done qty for each move line based on reservation lines
        lot_qty_map = {}
        for line in self.reservation_line_ids:
            if line.lot_id and line.qty > 0:
                lot_qty_map[line.lot_id.id] = lot_qty_map.get(line.lot_id.id, 0.0) + line.qty

        for ml in picking.move_line_ids:
            if ml.lot_id and ml.lot_id.id in lot_qty_map:
                ml.qty_done = lot_qty_map[ml.lot_id.id]

        # Validate
        picking.button_validate()
        self.state = 'shipped'
        return self.action_open_picking()

    def action_create_invoice(self):
        self.ensure_one()
        if self.invoice_id:
            return self.action_open_invoice()
        if not self.sale_order_id:
            raise UserError(_("Create Sale Order first."))

        invoices = self.sale_order_id._create_invoices()
        if not invoices:
            raise UserError(_("No invoice created."))
        self.invoice_id = invoices[0].id
        self.state = 'invoiced'
        return self.action_open_invoice()

    def action_open_invoice(self):
        self.ensure_one()
        if not self.invoice_id:
            raise UserError(_("No Invoice linked yet."))
        return {
            'type': 'ir.actions.act_window',
            'name': _('Invoice'),
            'res_model': 'account.move',
            'res_id': self.invoice_id.id,
            'view_mode': 'form',
            'target': 'current',
        }

    def action_open_picking(self):
        self.ensure_one()
        if not self.picking_id:
            raise UserError(_("No Delivery Picking linked yet."))
        return {
            'type': 'ir.actions.act_window',
            'name': _('Delivery'),
            'res_model': 'stock.picking',
            'res_id': self.picking_id.id,
            'view_mode': 'form',
            'target': 'current',
        }


class AgriShipmentReservation(models.Model):
    _name = 'agri.shipment.reservation'
    _description = 'Shipment Lot Reservation'

    shipment_id = fields.Many2one('agri.export.shipment', ondelete='cascade', required=True)

    lot_id = fields.Many2one('stock.lot', required=True)
    product_id = fields.Many2one('product.product', related='lot_id.product_id', store=True, readonly=True)

    qty = fields.Float(required=True)
