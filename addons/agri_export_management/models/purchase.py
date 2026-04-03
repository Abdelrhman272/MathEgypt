from odoo import api, fields, models


class PurchaseOrder(models.Model):
    _inherit = "purchase.order"

    agx_evaluation_id = fields.Many2one("agx.evaluation", string="Export Evaluation", index=True)


class StockPicking(models.Model):
    _inherit = "stock.picking"

    agx_evaluation_id = fields.Many2one("agx.evaluation", string="Export Evaluation", compute="_compute_agx_evaluation_id", store=True, readonly=True)
    agx_flow_type = fields.Selection([
        ("incoming", "Incoming"),
        ("batch_consume", "Batch Consumption"),
        ("batch_output", "Batch Output"),
        ("shipment", "Shipment"),
    ], string="AGX Flow Type", compute="_compute_agx_flow_type", store=True, readonly=True)

    @api.depends("purchase_id.agx_evaluation_id", "move_ids.agx_batch_id", "picking_type_id.code")
    def _compute_agx_evaluation_id(self):
        for rec in self:
            eval_id = rec.purchase_id.agx_evaluation_id
            if not eval_id and rec.move_ids.filtered("agx_batch_id"):
                eval_id = rec.move_ids.filtered("agx_batch_id")[0].agx_batch_id.evaluation_id
            rec.agx_evaluation_id = eval_id

    @api.depends("picking_type_id.code", "move_ids.agx_batch_id", "move_ids.agx_shipment_id")
    def _compute_agx_flow_type(self):
        for rec in self:
            if rec.picking_type_id.code == "incoming":
                rec.agx_flow_type = "incoming"
            elif rec.move_ids.filtered(lambda m: m.agx_flow_type == "consume"):
                rec.agx_flow_type = "batch_consume"
            elif rec.move_ids.filtered(lambda m: m.agx_flow_type == "output"):
                rec.agx_flow_type = "batch_output"
            elif rec.move_ids.filtered("agx_shipment_id"):
                rec.agx_flow_type = "shipment"
            else:
                rec.agx_flow_type = False


class StockMove(models.Model):
    _inherit = "stock.move"

    agx_batch_id = fields.Many2one("agx.batch", string="Production Batch", index=True)
    agx_shipment_id = fields.Many2one("agx.shipment", string="Shipment", index=True)
    agx_flow_type = fields.Selection([
        ("consume", "Consume"),
        ("output", "Output"),
        ("shipment", "Shipment"),
    ], string="AGX Flow Type")
