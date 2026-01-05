from odoo import models, fields, _
from odoo.exceptions import UserError


class MrpProduction(models.Model):
    _inherit = 'mrp.production'

    evaluation_id = fields.Many2one('agri.farm.evaluation', string="Farm Evaluation")
    agri_batch_id = fields.Many2one('agri.production.batch', string="Agri Batch", readonly=True)

    def button_mark_done(self):
        res = super().button_mark_done()
        for mo in self:
            if not mo.evaluation_id:
                continue

            # Create or update batch
            batch = mo.agri_batch_id
            if not batch:
                if not mo.move_raw_ids:
                    raise UserError(_("MO has no raw moves to identify a raw lot."))
                # pick first raw lot if exists on move lines
                raw_lot = False
                for ml in mo.move_raw_ids.move_line_ids:
                    if ml.lot_id:
                        raw_lot = ml.lot_id
                        break
                if not raw_lot:
                    # If no lot tracked, still allow but require manual in batch
                    raw_lot = False

                batch = self.env['agri.production.batch'].create({
                    'evaluation_id': mo.evaluation_id.id,
                    'mrp_production_id': mo.id,
                    'raw_lot_id': raw_lot.id if raw_lot else False,
                    'expected_qty': mo.evaluation_id.expected_qty or 0.0,
                    'state': 'draft',
                })
                mo.agri_batch_id = batch.id

            # Sync outputs from finished move lines (lots + qty_done)
            batch.action_sync_from_mo()
        return res
