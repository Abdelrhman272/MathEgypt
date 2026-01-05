from odoo import models, fields, api, _
from odoo.exceptions import UserError


class AgriFarmEvaluation(models.Model):
    _name = 'agri.farm.evaluation'
    _description = 'Farm Evaluation'
    _inherit = ['mail.thread']

    name = fields.Char(
        default=lambda self: self.env['ir.sequence'].next_by_code('agri.farm.evaluation'),
        readonly=True
    )
    farm_id = fields.Many2one('agri.farm', required=True)
    crop_id = fields.Many2one('agri.crop')
    evaluation_date = fields.Date(required=True)

    # Planning
    expected_qty = fields.Float()
    expected_uom_id = fields.Many2one('uom.uom')

    # Purchase proposal fields (generic)
    supplier_id = fields.Many2one('res.partner', domain=[('supplier_rank', '>', 0)])
    purchase_product_id = fields.Many2one('product.product')
    purchase_qty = fields.Float(default=0.0)
    purchase_uom_id = fields.Many2one('uom.uom')
    purchase_price_unit = fields.Float(default=0.0)

    purchase_id = fields.Many2one('purchase.order', readonly=True)
    state = fields.Selection(
        [('draft', 'Draft'), ('confirmed', 'Confirmed'), ('cancelled', 'Cancelled')],
        default='draft',
        tracking=True
    )
    note = fields.Text()

    production_ids = fields.One2many('agri.production.batch', 'evaluation_id', readonly=True)
    production_count = fields.Integer(compute='_compute_counts')
    shipment_ids = fields.One2many('agri.export.shipment', 'evaluation_id', readonly=True)
    shipment_count = fields.Integer(compute='_compute_counts')

    @api.depends('production_ids', 'shipment_ids')
    def _compute_counts(self):
        for rec in self:
            rec.production_count = len(rec.production_ids)
            rec.shipment_count = len(rec.shipment_ids)

    def action_create_purchase(self):
        self.ensure_one()
        if self.purchase_id:
            return self.action_open_purchase()

        if not self.supplier_id:
            raise UserError(_("Please set Supplier on the evaluation."))
        if not self.purchase_product_id:
            raise UserError(_("Please set Purchase Product on the evaluation."))
        if not self.purchase_uom_id:
            # fallback to product uom
            self.purchase_uom_id = self.purchase_product_id.uom_po_id or self.purchase_product_id.uom_id
        if self.purchase_qty <= 0:
            raise UserError(_("Purchase Qty must be > 0."))

        po = self.env['purchase.order'].create({
            'partner_id': self.supplier_id.id,
            'origin': self.name,
        })
        self.env['purchase.order.line'].create({
            'order_id': po.id,
            'product_id': self.purchase_product_id.id,
            'name': self.purchase_product_id.display_name,
            'product_qty': self.purchase_qty,
            'product_uom': self.purchase_uom_id.id,
            'price_unit': self.purchase_price_unit,
        })

        self.purchase_id = po.id
        self.state = 'confirmed'

        return {
            'type': 'ir.actions.act_window',
            'name': _('Purchase Order'),
            'res_model': 'purchase.order',
            'res_id': po.id,
            'view_mode': 'form',
            'target': 'current',
        }

    def action_open_purchase(self):
        self.ensure_one()
        if not self.purchase_id:
            raise UserError(_("No Purchase Order linked yet."))
        return {
            'type': 'ir.actions.act_window',
            'name': _('Purchase Order'),
            'res_model': 'purchase.order',
            'res_id': self.purchase_id.id,
            'view_mode': 'form',
            'target': 'current',
        }

    def action_open_production(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Production Batches'),
            'res_model': 'agri.production.batch',
            'view_mode': 'list,form',
            'domain': [('evaluation_id', '=', self.id)],
            'target': 'current',
        }

    def action_open_shipments(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Shipments'),
            'res_model': 'agri.export.shipment',
            'view_mode': 'list,form',
            'domain': [('evaluation_id', '=', self.id)],
            'target': 'current',
        }
