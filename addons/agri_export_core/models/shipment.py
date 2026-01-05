
from odoo import models, fields

class AgriExportShipment(models.Model):
    _name = 'agri.export.shipment'
    _description = 'Export Shipment'

    name = fields.Char(default=lambda self: self.env['ir.sequence'].next_by_code('agri.export.shipment'), readonly=True)
    container_no = fields.Char(required=True)
    sale_order_id = fields.Many2one('sale.order')
    invoice_id = fields.Many2one('account.move')
    lot_ids = fields.Many2many('stock.lot')
    state = fields.Selection([('draft','Draft'),('shipped','Shipped'),('invoiced','Invoiced')], default='draft')
