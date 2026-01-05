
from odoo import models, fields, api

class AgriProductionBatch(models.Model):
    _name = 'agri.production.batch'
    _description = 'Production Batch'

    name = fields.Char(default=lambda self: self.env['ir.sequence'].next_by_code('agri.production.batch'), readonly=True)
    evaluation_id = fields.Many2one('agri.farm.evaluation')
    raw_lot_id = fields.Many2one('stock.lot', required=True)
    expected_qty = fields.Float()
    output_line_ids = fields.One2many('agri.production.output','batch_id')
    actual_output_qty = fields.Float(compute='_compute_qty', store=True)
    variance_qty = fields.Float(compute='_compute_qty', store=True)
    state = fields.Selection([('draft','Draft'),('done','Done')], default='draft')

    @api.depends('output_line_ids.qty')
    def _compute_qty(self):
        for r in self:
            total = sum(r.output_line_ids.mapped('qty'))
            r.actual_output_qty = total
            r.variance_qty = total - (r.expected_qty or 0.0)

class AgriProductionOutput(models.Model):
    _name = 'agri.production.output'
    _description = 'Production Output'

    batch_id = fields.Many2one('agri.production.batch', ondelete='cascade')
    product_id = fields.Many2one('product.product', required=True)
    lot_id = fields.Many2one('stock.lot', required=True)
    qty = fields.Float(required=True)
    uom_id = fields.Many2one('uom.uom', required=True)
