from odoo import _, api, fields, models
from odoo.exceptions import UserError


class PurchaseOrder(models.Model):
    _inherit = "purchase.order"

    agx_evaluation_id = fields.Many2one("agx.evaluation", string="Farm Evaluation", index=True, copy=False)


class StockPicking(models.Model):
    _inherit = "stock.picking"

    agx_evaluation_id = fields.Many2one("agx.evaluation", string="Farm Evaluation", compute="_compute_agx_evaluation_id", store=True, readonly=True)
    agx_flow_type = fields.Selection([
        ("incoming", "Incoming"),
        ("batch_consume", "Batch Consumption"),
        ("batch_output", "Batch Output"),
        ("shipment", "Shipment Delivery"),
    ], string="AGX Flow Type", compute="_compute_agx_flow_type", store=True, readonly=True)
    agx_batch_id = fields.Many2one("agx.batch", string="Production Batch", index=True, copy=False)
    agx_shipment_id = fields.Many2one("agx.shipment", string="Shipment", index=True, copy=False)

    @api.depends("purchase_id.agx_evaluation_id", "agx_batch_id.evaluation_id", "move_ids.agx_batch_id", "picking_type_id.code")
    def _compute_agx_evaluation_id(self):
        for rec in self:
            eval_id = rec.purchase_id.agx_evaluation_id
            if not eval_id and rec.agx_batch_id:
                eval_id = rec.agx_batch_id.evaluation_id
            if not eval_id and rec.move_ids.filtered("agx_batch_id"):
                eval_id = rec.move_ids.filtered("agx_batch_id")[0].agx_batch_id.evaluation_id
            rec.agx_evaluation_id = eval_id

    @api.depends("picking_type_id.code", "agx_batch_id", "agx_shipment_id", "move_ids.agx_batch_id", "move_ids.agx_shipment_id")
    def _compute_agx_flow_type(self):
        for rec in self:
            if rec.picking_type_id.code == "incoming":
                rec.agx_flow_type = "incoming"
            elif rec.agx_batch_id and rec.move_ids.filtered(lambda m: m.agx_flow_type == "consume"):
                rec.agx_flow_type = "batch_consume"
            elif rec.agx_batch_id and rec.move_ids.filtered(lambda m: m.agx_flow_type == "output"):
                rec.agx_flow_type = "batch_output"
            elif rec.agx_shipment_id or rec.move_ids.filtered("agx_shipment_id"):
                rec.agx_flow_type = "shipment"
            else:
                rec.agx_flow_type = False

    def _agx_has_evaluation_context(self):
        self.ensure_one()
        return bool(self.agx_evaluation_id or self.purchase_id.agx_evaluation_id)

    def _agx_sync_incoming_raw_destination(self):
        for rec in self.filtered(lambda p: p.picking_type_id.code == "incoming" and p.state not in ("done", "cancel")):
            if not rec._agx_has_evaluation_context():
                continue
            raw_location = rec.company_id.agx_raw_material_location_id
            if not raw_location:
                continue
            if rec.location_dest_id != raw_location:
                rec.with_context(agx_skip_raw_sync=True).write({"location_dest_id": raw_location.id})
            pending_moves = rec.move_ids.filtered(lambda m: m.state not in ("done", "cancel") and m.location_dest_id != raw_location)
            if pending_moves:
                pending_moves.write({"location_dest_id": raw_location.id})
            pending_move_lines = rec.move_line_ids.filtered(lambda ml: ml.state not in ("done", "cancel") and ml.location_dest_id != raw_location)
            if pending_move_lines:
                pending_move_lines.write({"location_dest_id": raw_location.id})

    @api.model_create_multi
    def create(self, vals_list):
        pickings = super().create(vals_list)
        pickings._agx_sync_incoming_raw_destination()
        return pickings

    def write(self, vals):
        res = super().write(vals)
        if not self.env.context.get("agx_skip_raw_sync"):
            self._agx_sync_incoming_raw_destination()
        return res

    def button_validate(self):
        for rec in self.filtered(lambda p: p.picking_type_id.code == "incoming"):
            if not rec._agx_has_evaluation_context():
                continue
            if not rec.company_id.agx_raw_material_location_id:
                raise UserError(_("Please configure Raw Material Location in Agricultural Export Settings before validating AGX incoming receipts."))
        self._agx_sync_incoming_raw_destination()
        return super().button_validate()


class StockMove(models.Model):
    _inherit = "stock.move"

    agx_batch_id = fields.Many2one("agx.batch", string="Production Batch", index=True)
    agx_shipment_id = fields.Many2one("agx.shipment", string="Shipment", index=True)
    agx_shipment_line_id = fields.Many2one("agx.shipment.line", string="Shipment Line", index=True)
    agx_flow_type = fields.Selection([
        ("consume", "Consume"),
        ("output", "Output"),
        ("shipment", "Shipment"),
    ], string="AGX Flow Type")
