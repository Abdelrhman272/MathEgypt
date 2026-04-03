from odoo import _, api, fields, models


class PurchaseOrder(models.Model):
    _inherit = 'purchase.order'

    agx_evaluation_id = fields.Many2one('agx.evaluation', string='Farm Evaluation', copy=False, index=True)

    @api.onchange('agx_evaluation_id')
    def _onchange_agx_evaluation_id(self):
        for rec in self:
            if rec.agx_evaluation_id:
                if not rec.partner_id and rec.agx_evaluation_id.partner_id:
                    rec.partner_id = rec.agx_evaluation_id.partner_id
                if not rec.origin:
                    rec.origin = rec.agx_evaluation_id.name


class StockPicking(models.Model):
    _inherit = 'stock.picking'

    agx_evaluation_id = fields.Many2one('agx.evaluation', string='Farm Evaluation', compute='_compute_agx_links', store=True, index=True)
    agx_batch_id = fields.Many2one('agx.batch', string='Production Batch', index=True, copy=False)
    agx_shipment_id = fields.Many2one('agx.shipment', string='Shipment', index=True, copy=False)
    agx_flow_type = fields.Selection([('batch_input', 'Batch Input'), ('batch_output', 'Batch Output'), ('shipment_delivery', 'Shipment Delivery')], string='AGX Flow', copy=False)

    @api.depends('move_ids.purchase_line_id.order_id.agx_evaluation_id')
    def _compute_agx_links(self):
        for rec in self:
            evaluation = rec.move_ids.mapped('purchase_line_id.order_id.agx_evaluation_id')[:1]
            rec.agx_evaluation_id = evaluation.id if evaluation else False


class AccountMove(models.Model):
    _inherit = 'account.move'

    agx_shipment_id = fields.Many2one('agx.shipment', string='Shipment', copy=False, index=True)
