from odoo import api, fields, models


class PurchaseOrder(models.Model):
    _inherit = "purchase.order"

    agx_evaluation_id = fields.Many2one(
        "agx.evaluation",
        string="Farm Evaluation",
        index=True,
        copy=False,
    )


class StockPicking(models.Model):
    _inherit = "stock.picking"

    agx_evaluation_id = fields.Many2one(
        "agx.evaluation",
        string="Farm Evaluation",
        compute="_compute_agx_evaluation_id",
        store=True,
        readonly=True,
    )
    # FIX: agx_flow_type was fully computed from move_ids which don't exist yet
    # when the picking is first created. Now it is a stored regular field set
    # explicitly by the batch/shipment creation code, with a compute fallback
    # only for incoming receipts that come from the purchase flow.
    agx_flow_type = fields.Selection(
        [
            ("incoming", "Incoming"),
            ("batch_consume", "Batch Consumption"),
            ("batch_output", "Batch Output"),
            ("shipment", "Shipment Delivery"),
        ],
        string="AGX Flow Type",
        compute="_compute_agx_flow_type",
        store=True,
        readonly=False,   # allow batch/shipment code to write directly
    )
    # FIX: agx_batch_id must be a plain stored field so One2many on agx.batch works.
    agx_batch_id = fields.Many2one(
        "agx.batch",
        string="Production Batch",
        index=True,
        copy=False,
        store=True,
    )
    agx_shipment_id = fields.Many2one(
        "agx.shipment",
        string="Shipment",
        index=True,
        copy=False,
        store=True,
    )

    @api.depends(
        "purchase_id.agx_evaluation_id",
        "agx_batch_id.evaluation_id",
        "move_ids.agx_batch_id",
    )
    def _compute_agx_evaluation_id(self):
        for rec in self:
            eval_id = rec.purchase_id.agx_evaluation_id
            if not eval_id and rec.agx_batch_id:
                eval_id = rec.agx_batch_id.evaluation_id
            if not eval_id:
                batch_moves = rec.move_ids.filtered("agx_batch_id")
                if batch_moves:
                    eval_id = batch_moves[0].agx_batch_id.evaluation_id
            rec.agx_evaluation_id = eval_id

    @api.depends(
        "picking_type_id.code",
        "agx_batch_id",
        "agx_shipment_id",
        "move_ids.agx_flow_type",
    )
    def _compute_agx_flow_type(self):
        """Compute is kept as a lightweight fallback for incoming receipts.
        Batch and shipment creation code sets agx_flow_type directly via write,
        so this only fires for records that have not been set explicitly yet."""
        for rec in self:
            # Don't overwrite a value that was already set explicitly
            if rec.agx_flow_type and rec.agx_flow_type != "incoming":
                continue
            if rec.picking_type_id.code == "incoming":
                rec.agx_flow_type = "incoming"
            elif rec.agx_batch_id:
                consume_moves = rec.move_ids.filtered(
                    lambda m: m.agx_flow_type == "consume"
                )
                output_moves = rec.move_ids.filtered(
                    lambda m: m.agx_flow_type == "output"
                )
                if consume_moves:
                    rec.agx_flow_type = "batch_consume"
                elif output_moves:
                    rec.agx_flow_type = "batch_output"
            elif rec.agx_shipment_id or rec.move_ids.filtered("agx_shipment_id"):
                rec.agx_flow_type = "shipment"
            else:
                rec.agx_flow_type = False


class StockMove(models.Model):
    _inherit = "stock.move"

    agx_batch_id = fields.Many2one(
        "agx.batch",
        string="Production Batch",
        index=True,
    )
    agx_shipment_id = fields.Many2one(
        "agx.shipment",
        string="Shipment",
        index=True,
    )
    agx_shipment_line_id = fields.Many2one(
        "agx.shipment.line",
        string="Shipment Line",
        index=True,
    )
    agx_flow_type = fields.Selection(
        [
            ("consume", "Consume"),
            ("output", "Output"),
            ("shipment", "Shipment"),
        ],
        string="AGX Flow Type",
    )
