# -*- coding: utf-8 -*-
# Copyright 2025 NextGen Systems — OPL-1
"""Purchase and stock model extensions for Agricultural Export Management.

Extensions defined here:
  StockPicking — Adds auto lot generation on incoming receipt validation
                 when agx_auto_generate_lot_numbers is enabled in Settings.
"""
"""
purchase.py — Odoo Stock / Purchase Model Extensions
======================================================
This module extends three native Odoo models with AGX-specific fields
so that every stock movement in the system can be traced back to the
agricultural export workflow that generated it.

Extended models
---------------
PurchaseOrder   → agx_evaluation_id
                  Links the PO to the farm evaluation it was created from.

StockPicking    → agx_evaluation_id  (computed, stored)
                  agx_batch_id       (plain stored — NOT computed, so the
                                      One2many inverse FK exists in the DB)
                  agx_shipment_id    (plain stored)
                  agx_flow_type      (stored, writable — set at picking
                                      creation time, NOT computed post-hoc)

StockMove       → agx_batch_id
                  agx_shipment_id
                  agx_shipment_line_id
                  agx_flow_type

Design note on agx_flow_type
-----------------------------
The field is defined as compute+store+readonly=False so that:
  1. Batch and shipment creation code can write it directly at
     picking-creation time (before any moves exist).
  2. The compute fires for incoming receipts (where no explicit write
     happens) based on picking_type_id.code.
  3. Subsequent compute re-evaluations don't overwrite an already-set
     value (guarded by the ``if rec.agx_flow_type`` check).

This avoids the classic bug where the compute fires before move_ids
exist, leaving agx_flow_type = False on all batch/shipment pickings.
"""

from odoo import api, fields, models


# ---------------------------------------------------------------------------
# Purchase Order
# ---------------------------------------------------------------------------
class PurchaseOrder(models.Model):
    """Adds the farm evaluation link to purchase orders.

    When a purchase order is created from an evaluation (via
    ``AgxEvaluation.action_create_purchase_order``), this field is
    populated automatically.  It drives the ``agx_evaluation_id``
    compute on the resulting stock.picking (receipt).
    """

    _inherit = "purchase.order"

    agx_evaluation_id = fields.Many2one(
        "agx.evaluation",
        string="Farm Evaluation",
        index=True,
        copy=False,
        help="Farm evaluation this purchase order was raised from.",
    )


# ---------------------------------------------------------------------------
# Stock Picking
# ---------------------------------------------------------------------------
class StockPicking(models.Model):
    """Adds AGX traceability fields to stock transfers.

    Key design decisions
    --------------------
    * ``agx_batch_id`` and ``agx_shipment_id`` are plain stored Many2one
      fields (not computed) so that:
        - Odoo creates the inverse FK column in the DB.
        - ``agx.batch.stock_picking_ids`` One2many works correctly.
        - Writes from batch/shipment code are persisted immediately.

    * ``agx_flow_type`` is compute+store+readonly=False so it can be
      set explicitly at picking creation time.  The compute only sets
      it for incoming receipts or when it has not been set yet.
    """

    _inherit = "stock.picking"

    agx_evaluation_id = fields.Many2one(
        "agx.evaluation",
        string="Farm Evaluation",
        compute="_compute_agx_evaluation_id",
        store=True,
        readonly=True,
        help="Farm evaluation linked to this transfer (auto-derived).",
    )
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
        readonly=False,   # must be writable so batch/shipment code can set it
        help=(
            "Identifies which part of the AGX workflow created this transfer. "
            "Set explicitly at picking creation — not derived from moves."
        ),
    )
    # Plain stored fields — NOT computed — so One2many inverse FKs work
    agx_batch_id = fields.Many2one(
        "agx.batch",
        string="Production Batch",
        index=True,
        copy=False,
        store=True,
        help="Batch that generated this transfer.",
    )
    agx_shipment_id = fields.Many2one(
        "agx.shipment",
        string="Shipment",
        index=True,
        copy=False,
        store=True,
        help="Shipment that generated this delivery order.",
    )

    # ------------------------------------------------------------------
    # Computed: evaluation link
    # ------------------------------------------------------------------
    @api.depends(
        "purchase_id.agx_evaluation_id",
        "agx_batch_id.evaluation_id",
        "move_ids.agx_batch_id",
    )
    def _compute_agx_evaluation_id(self):
        """Derive the farm evaluation from the purchase order or batch."""
        for rec in self:
            eval_id = rec.purchase_id.agx_evaluation_id
            if not eval_id and rec.agx_batch_id:
                eval_id = rec.agx_batch_id.evaluation_id
            if not eval_id:
                batch_moves = rec.move_ids.filtered("agx_batch_id")
                if batch_moves:
                    eval_id = batch_moves[0].agx_batch_id.evaluation_id
            rec.agx_evaluation_id = eval_id

    # ------------------------------------------------------------------
    # Computed: flow type (lightweight fallback only)
    # ------------------------------------------------------------------
    @api.depends(
        "picking_type_id.code",
        "agx_batch_id",
        "agx_shipment_id",
        "move_ids.agx_flow_type",
    )
    def _compute_agx_flow_type(self):
        """Set agx_flow_type for incoming receipts and any unset records.

        Batch and shipment creation code writes agx_flow_type directly
        at picking-creation time.  This compute only handles:
          1. Incoming receipts (from purchase orders).
          2. Records where agx_flow_type was never explicitly set.

        It intentionally skips records that already have a non-incoming
        flow type to avoid overwriting values set by the creation code.
        """
        for rec in self:
            # Skip if already set by creation code
            if rec.agx_flow_type and rec.agx_flow_type != "incoming":
                continue
            if rec.picking_type_id.code == "incoming":
                rec.agx_flow_type = "incoming"
            elif rec.agx_batch_id:
                consume = rec.move_ids.filtered(
                    lambda m: m.agx_flow_type == "consume"
                )
                output = rec.move_ids.filtered(
                    lambda m: m.agx_flow_type == "output"
                )
                if consume:
                    rec.agx_flow_type = "batch_consume"
                elif output:
                    rec.agx_flow_type = "batch_output"
            elif rec.agx_shipment_id or rec.move_ids.filtered("agx_shipment_id"):
                rec.agx_flow_type = "shipment"
            else:
                rec.agx_flow_type = False


# ---------------------------------------------------------------------------
# Stock Move
# ---------------------------------------------------------------------------
class StockPicking(models.Model):
    """Extends stock.picking with AGX auto lot generation on receipts.

    When agx_auto_generate_lot_numbers is enabled in Settings and the
    picking is an incoming receipt linked to an AGX evaluation, any
    move line without a lot gets one auto-generated before validation.
    """

    _inherit = "stock.picking"

    def button_validate(self):
        """Auto-generate lot numbers on incoming AGX receipts before validation.

        Triggered when the user clicks Validate on a receipt.
        For each move line that:
          - belongs to an incoming receipt (code = incoming)
          - is linked to an AGX evaluation
          - has a product with lot tracking enabled
          - has no lot assigned yet
        a new stock.lot is created using the ir.sequence for serial numbers.
        """
        for picking in self.filtered(
            lambda p: p.picking_type_id.code == "incoming"
            and p.agx_evaluation_id
            and p.company_id.agx_auto_generate_lot_numbers
        ):
            for move_line in picking.move_line_ids.filtered(
                lambda ml: ml.product_id.tracking == "lot"
                and not ml.lot_id
            ):
                lot_name = (
                    self.env["ir.sequence"].next_by_code("stock.lot.serial")
                    or "LOT-{}-{}".format(
                        move_line.product_id.default_code or move_line.product_id.id,
                        fields.Datetime.now().strftime("%Y%m%d%H%M%S"),
                    )
                )
                move_line.lot_id = self.env["stock.lot"].create(
                    {
                        "name": lot_name,
                        "product_id": move_line.product_id.id,
                        "company_id": picking.company_id.id,
                    }
                )
        return super().button_validate()


class StockMove(models.Model):
    """Adds AGX traceability fields to individual stock moves.

    These fields allow filtering moves by batch or shipment in Odoo's
    standard inventory reports and the AGX traceability wizard.
    """

    _inherit = "stock.move"

    agx_batch_id = fields.Many2one(
        "agx.batch",
        string="Production Batch",
        index=True,
        help="Batch that generated this move.",
    )
    agx_shipment_id = fields.Many2one(
        "agx.shipment",
        string="Shipment",
        index=True,
        help="Shipment delivery this move belongs to.",
    )
    agx_shipment_line_id = fields.Many2one(
        "agx.shipment.line",
        string="Shipment Line",
        index=True,
        help="Specific shipment product line this move fulfils.",
    )
    agx_flow_type = fields.Selection(
        [
            ("consume", "Consume"),
            ("output", "Output"),
            ("shipment", "Shipment"),
        ],
        string="AGX Flow Type",
        help="Which AGX workflow step created this move.",
    )
