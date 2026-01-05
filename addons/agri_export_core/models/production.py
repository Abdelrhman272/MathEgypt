from odoo import models, fields, api, _
from odoo.exceptions import UserError


class AgriProductionBatch(models.Model):
    _name = 'agri.production.batch'
    _description = 'Production Batch'

    name = fields.Char(default=lambda self: self.env['ir.sequence'].next_by_code('agri.production.batch'), readonly=True)

    evaluation_id = fields.Many2one('agri.farm.evaluation')
    mrp_production_id = fields.Many2one('mrp.production')

    raw_lot_id = fields.Many2one('stock.lot', required=False)
    expected_qty = fields.Float()

    output_line_ids = fields.One2many('agri.production.output', 'batch_id')
    actual_output_qty = fields.Float(compute='_compute_qty', store=True)
    variance_qty = fields.Float(compute='_compute_qty', store=True)

    state = fields.Selection([('draft', 'Draft'), ('done', 'Done')], default='draft')

    @api.depends('output_line_ids.qty')
    def _compute_qty(self):
        for r in self:
            total = sum(r.output_line_ids.mapped('qty'))
            r.actual_output_qty = total
            r.variance_qty = total - (r.expected_qty or 0.0)

    def action_sync_from_mo(self):
        """Pull finished lots/qty from linked MO (after Done)"""
        self.ensure_one()
        mo = self.mrp_production_id
        if not mo:
            raise UserError(_("No MO linked to this batch."))

        # Clear old outputs then rebuild
        self.output_line_ids.unlink()

        # Read from finished move lines
        # Prefer move_finished_ids.move_line_ids (qty_done + lot_id)
        lines = mo.move_finished_ids.move_line_ids
        if not lines:
            # fallback: sometimes qty on moves without move lines
            for mv in mo.move_finished_ids:
                if mv.product_uom_qty > 0:
                    self.env['agri.production.output'].create({
                        'batch_id': self.id,
                        'product_id': mv.product_id.id,
                        'lot_id': False,
                        'qty': mv.quantity_done if hasattr(mv, 'quantity_done') else mv.product_uom_qty,
                        'uom_id': mv.product_uom.id,
                    })
            self.state = 'done'
            return True

        for ml in lines:
            if ml.qty_done <= 0:
                continue
            self.env['agri.production.output'].create({
                'batch_id': self.id,
                'product_id': ml.product_id.id,
                'lot_id': ml.lot_id.id,
                'qty': ml.qty_done,
                'uom_id': ml.product_uom_id.id,
            })

        self.state = 'done'
        return True


class AgriProductionOutput(models.Model):
    _name = 'agri.production.output'
    _description = 'Production Output'

    batch_id = fields.Many2one('agri.production.batch', ondelete='cascade', required=True)
    product_id = fields.Many2one('product.product', required=True)
    lot_id = fields.Many2one('stock.lot')
    qty = fields.Float(required=True)
    uom_id = fields.Many2one('uom.uom', required=True)
