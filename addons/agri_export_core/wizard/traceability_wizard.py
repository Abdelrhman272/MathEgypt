
from odoo import models, fields

class AgriTraceabilityWizard(models.TransientModel):
    _name = 'agri.traceability.wizard'
    invoice_id = fields.Many2one('account.move')

    def action_print(self):
        return self.env.ref('agri_export_core.action_traceability_report').report_action(self)
