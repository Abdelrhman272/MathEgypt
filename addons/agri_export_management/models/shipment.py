# -*- coding: utf-8 -*-
"""
shipment.py — Export Shipment Models
======================================
Manages the full lifecycle of an agricultural export shipment:
  Draft → Reserved → Shipped (or Cancelled)

Models in this file
-------------------
AgxShipment          : main shipment header
AgxShipmentContainer : individual container records (synced with header count)
AgxShipmentLine      : product lines per container
AgxShipmentLotLine   : reserved lot lines (populated by action_reserve)
AgxShipmentCostLine  : logistics cost lines with multi-source support

Stock movement flow
-------------------
  Reserve  → creates delivery stock.picking (outgoing)
               Finished Goods / Cold Storage → Customer Location
             and populates agx_shipment_lot_line records with the lots
             to be shipped.

  Shipped  → builds move lines from lot_line_ids, validates the picking,
             and marks the shipment Done.

Reserve double-reservation fix
-------------------------------
The old code called ``action_assign()`` on an already-confirmed picking
which raised "Nothing to check availability".  The fix:
  1. ``action_reserve`` calls ``do_unreserve()`` on any existing delivery
     picking BEFORE rebuilding it, cleanly releasing ``stock.quant``
     reserved_quantity.
  2. ``action_ship`` no longer calls ``action_assign()`` at all — move
     lines are constructed manually from ``lot_line_ids``.

Analytic accounting
-------------------
When a season is linked, the delivery picking carries the season's
analytic account so logistics costs post to the correct season P&L.

Profitability calculation
-------------------------
  revenue_amount      = sale_order_id.amount_untaxed
  total_logistics_cost = sum of cost_line_ids.effective_amount
  gross_profit_amount  = revenue_amount - total_logistics_cost
  gross_margin_pct     = gross_profit / revenue × 100

Note: gross_profit can be negative (logistics > revenue).  This is
intentional — it signals a loss-making shipment that needs attention.
"""

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError


class AgxShipment(models.Model):
    """Export shipment — from container booking to delivery validation."""

    _name = "agx.shipment"
    _description = "Export Shipment"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "shipment_date desc, id desc"

    # ------------------------------------------------------------------
    # Identity
    # ------------------------------------------------------------------
    name = fields.Char(
        default=lambda self: _("New"),
        copy=False,
        readonly=True,
        help="Auto-generated from the AGX Shipment sequence.",
    )
    shipment_date = fields.Date(
        default=fields.Date.context_today, tracking=True
    )
    company_id = fields.Many2one(
        "res.company",
        required=True,
        default=lambda self: self.env.company,
        index=True,
    )
    currency_id = fields.Many2one(
        "res.currency",
        related="company_id.currency_id",
        store=True,
        readonly=True,
    )

    # ------------------------------------------------------------------
    # Core references
    # ------------------------------------------------------------------
    customer_id = fields.Many2one(
        "res.partner",
        string="Customer",
        tracking=True,
    )
    destination_id = fields.Many2one(
        "agx.destination",
        tracking=True,
    )
    season_id = fields.Many2one(
        "agx.season",
        string="Season",
        tracking=True,
        index=True,
        help="Production season this shipment belongs to.",
    )

    # ------------------------------------------------------------------
    # Container info
    # ------------------------------------------------------------------
    container_no = fields.Char(
        tracking=True,
        help="First container number (synced from the Containers tab).",
    )
    container_count = fields.Integer(
        default=1,
        tracking=True,
        help="Number of containers; drives auto-creation of container records.",
    )
    seal_no = fields.Char(
        help="Seal number of the first container.",
    )

    # ------------------------------------------------------------------
    # Freight / shipping details
    # ------------------------------------------------------------------
    vessel_name = fields.Char(
        string="Vessel",
        help="Name of the vessel carrying this shipment.",
    )
    voyage_no = fields.Char(
        string="Voyage No",
    )
    etd = fields.Date(
        string="ETD",
        help="Estimated Time of Departure from port.",
    )
    eta = fields.Date(
        string="ETA",
        help="Estimated Time of Arrival at destination.",
    )
    bl_number = fields.Char(
        string="B/L Number",
        help="Bill of Lading reference number.",
    )

    # ------------------------------------------------------------------
    # State
    # ------------------------------------------------------------------
    state = fields.Selection(
        [
            ("draft", "Draft"),
            ("reserved", "Reserved"),
            ("shipped", "Shipped"),
            ("cancelled", "Cancelled"),
        ],
        default="draft",
        tracking=True,
    )

    # ------------------------------------------------------------------
    # Lines
    # ------------------------------------------------------------------
    container_ids = fields.One2many(
        "agx.shipment.container",
        "shipment_id",
        string="Containers",
        copy=True,
    )
    line_ids = fields.One2many(
        "agx.shipment.line",
        "shipment_id",
        string="Products",
        copy=True,
    )
    lot_line_ids = fields.One2many(
        "agx.shipment.lot.line",
        "shipment_id",
        string="Reserved Lots",
        copy=True,
    )
    cost_line_ids = fields.One2many(
        "agx.shipment.cost.line",
        "shipment_id",
        string="Logistics Costs",
        copy=True,
    )

    # ------------------------------------------------------------------
    # Linked documents
    # ------------------------------------------------------------------
    sale_order_id = fields.Many2one("sale.order", copy=False)
    delivery_picking_id = fields.Many2one(
        "stock.picking",
        copy=False,
        readonly=True,
        help="The outgoing delivery order created by Reserve.",
    )

    # ------------------------------------------------------------------
    # Computed: profitability
    # ------------------------------------------------------------------
    total_qty = fields.Float(
        compute="_compute_profitability",
        store=True,
        help="Sum of all product line quantities.",
    )
    total_reserved_qty = fields.Float(
        compute="_compute_profitability",
        store=True,
        help="Sum of all reserved lot quantities.",
    )
    total_logistics_cost = fields.Monetary(
        currency_field="currency_id",
        compute="_compute_profitability",
        store=True,
        help="Sum of all logistics cost lines.",
    )
    cost_per_container = fields.Monetary(
        currency_field="currency_id",
        compute="_compute_profitability",
        store=True,
        help="total_logistics_cost ÷ container_count.",
    )
    revenue_amount = fields.Monetary(
        currency_field="currency_id",
        compute="_compute_profitability",
        store=True,
        help="Sales Order untaxed amount.",
    )
    gross_profit_amount = fields.Monetary(
        currency_field="currency_id",
        compute="_compute_profitability",
        store=True,
        help="revenue_amount − total_logistics_cost.",
    )
    gross_margin_pct = fields.Float(
        compute="_compute_profitability",
        store=True,
        digits=(16, 2),
        help="(gross_profit / revenue) × 100.  Can be negative.",
    )
    note = fields.Html()

    # ------------------------------------------------------------------
    # Computed: profitability
    # ------------------------------------------------------------------
    @api.depends(
        "line_ids.product_qty",
        "lot_line_ids.reserved_qty",
        "cost_line_ids.effective_amount",
        "sale_order_id.amount_untaxed",
        "container_count",
    )
    def _compute_profitability(self):
        """Compute all financial KPIs from lines, lots, costs and SO."""
        for rec in self:
            rec.total_qty = sum(rec.line_ids.mapped("product_qty"))
            rec.total_reserved_qty = sum(rec.lot_line_ids.mapped("reserved_qty"))
            rec.total_logistics_cost = sum(
                rec.cost_line_ids.mapped("effective_amount")
            )
            effective_count = rec.container_count or len(rec.container_ids) or 1
            rec.cost_per_container = (
                rec.total_logistics_cost / effective_count
                if effective_count
                else 0.0
            )
            rec.revenue_amount = (
                rec.sale_order_id.amount_untaxed
                if rec.sale_order_id
                else 0.0
            )
            rec.gross_profit_amount = (
                rec.revenue_amount - rec.total_logistics_cost
            )
            rec.gross_margin_pct = (
                (rec.gross_profit_amount / rec.revenue_amount * 100.0)
                if rec.revenue_amount
                else 0.0
            )

    # ------------------------------------------------------------------
    # ORM overrides
    # ------------------------------------------------------------------
    @api.model_create_multi
    def create(self, vals_list):
        """Auto-assign sequence and create initial container records."""
        for vals in vals_list:
            if vals.get("name", _("New")) == _("New"):
                vals["name"] = (
                    self.env["ir.sequence"].next_by_code("agx.shipment")
                    or _("New")
                )
        records = super().create(vals_list)
        records._sync_container_setup()
        return records

    def write(self, vals):
        """Sync container records when container_count changes."""
        res = super().write(vals)
        if not self.env.context.get("agx_skip_container_sync"):
            self._sync_container_setup()
        return res

    # ------------------------------------------------------------------
    # Container sync
    # ------------------------------------------------------------------
    def _sync_container_setup(self):
        """Keep agx.shipment.container records in sync with container_count.

        - Creates missing containers when count increases.
        - Syncs container_no and seal_no between header and first container.
        Does NOT delete containers when count decreases to preserve data.
        """
        Container = self.env["agx.shipment.container"]
        for rec in self:
            target = rec.container_count or 1
            if not rec.container_ids:
                for i in range(target):
                    Container.create(
                        {
                            "shipment_id": rec.id,
                            "sequence": (i + 1) * 10,
                            "name": "{}-C{}".format(rec.name, i + 1),
                            "container_no": rec.container_no if i == 0 else False,
                            "seal_no": rec.seal_no if i == 0 else False,
                        }
                    )
            elif len(rec.container_ids) < target:
                existing = len(rec.container_ids)
                for i in range(existing, target):
                    Container.create(
                        {
                            "shipment_id": rec.id,
                            "sequence": (i + 1) * 10,
                            "name": "{}-C{}".format(rec.name, i + 1),
                        }
                    )
            # Sync no / seal between header and first container
            if rec.container_ids:
                first = rec.container_ids.sorted("sequence")[0]
                update = {}
                if rec.container_no and first.container_no != rec.container_no:
                    update["container_no"] = rec.container_no
                if rec.seal_no and first.seal_no != rec.seal_no:
                    update["seal_no"] = rec.seal_no
                if update:
                    first.write(update)
                header = {}
                if not rec.container_no and first.container_no:
                    header["container_no"] = first.container_no
                if not rec.seal_no and first.seal_no:
                    header["seal_no"] = first.seal_no
                if rec.container_count != len(rec.container_ids):
                    header["container_count"] = len(rec.container_ids)
                if header:
                    rec.with_context(agx_skip_container_sync=True).write(header)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _quantity_field_name(self, model):
        """Detect the correct quantity field name for the Odoo version."""
        return "quantity" if "quantity" in model._fields else "qty_done"

    def _get_default_stock_location(self):
        """Return first internal location (deterministic order)."""
        self.ensure_one()
        return self.env["stock.location"].search(
            [
                ("company_id", "in", [False, self.company_id.id]),
                ("usage", "=", "internal"),
            ],
            limit=1,
            order="company_id desc, id",
        )

    def _get_finished_goods_location(self):
        """Return the source location for shipment delivery pickings.

        Priority:
          1. Cold Storage Location (if configured) — goods are typically
             refrigerated after packing and before export.
          2. Finished Goods Location.
          3. First internal location (last resort).
        """
        self.ensure_one()
        return (
            self.company_id.agx_cold_storage_location_id
            or self.company_id.agx_finished_goods_location_id
            or self._get_default_stock_location()
        )

    def _get_customer_location(self):
        """Return the customer virtual location for outgoing moves."""
        self.ensure_one()
        return self.env["stock.location"].search(
            [
                ("usage", "=", "customer"),
                ("company_id", "in", [False, self.company_id.id]),
            ],
            limit=1,
        )

    def _get_outgoing_picking_type(self):
        """Return the configured outgoing delivery picking type."""
        self.ensure_one()
        return self.company_id.agx_outgoing_picking_type_id or self.env[
            "stock.picking.type"
        ].search(
            [
                ("code", "=", "outgoing"),
                ("company_id", "in", [False, self.company_id.id]),
            ],
            limit=1,
            order="company_id desc,id",
        )

    def _validate_delivery_settings(self):
        """Check all required locations and picking types are configured.

        Returns (source_location, customer_location, outgoing_type).
        Raises UserError if any is missing.
        """
        self.ensure_one()
        source = self._get_finished_goods_location()
        customer = self._get_customer_location()
        out_type = self._get_outgoing_picking_type()
        if not source or not customer or not out_type:
            raise UserError(
                _(
                    "Please configure Finished Goods Location (or Cold Storage) "
                    "and Outgoing Delivery Type in Agricultural Export Settings."
                )
            )
        return source, customer, out_type

    def _normalize_line_containers(self):
        """Assign the first container to any product line that has none."""
        for rec in self:
            fallback = rec.container_ids.sorted("sequence")[:1]
            fallback = fallback[0] if fallback else False
            if fallback:
                for line in rec.line_ids.filtered(lambda l: not l.container_id):
                    line.container_id = fallback.id

    # ------------------------------------------------------------------
    # Reservation helpers
    # ------------------------------------------------------------------
    def _lock_reservation_scope(self, candidate_quant_ids, lot_pairs):
        """Acquire row-level DB locks to prevent concurrent reservation races.

        Locks:
          - The shipment row itself.
          - The candidate stock.quant rows.
          - All agx_shipment_lot_line rows for the same product/lot pairs
            across all non-cancelled shipments.

        Must be called inside a transaction (always true in Odoo ORM).
        """
        self.ensure_one()
        candidate_quant_ids = tuple(sorted(set(candidate_quant_ids or [])))
        lot_pairs = sorted(set(lot_pairs or []))
        self.env["stock.quant"].flush_model(
            ["company_id", "product_id", "lot_id",
             "quantity", "reserved_quantity", "location_id"]
        )
        self.env["agx.shipment.lot.line"].flush_model(
            ["shipment_id", "product_id", "lot_id", "reserved_qty"]
        )
        self.env.cr.execute(
            "SELECT id FROM agx_shipment WHERE id = %s FOR UPDATE",
            (self.id,),
        )
        if candidate_quant_ids:
            self.env.cr.execute(
                """
                SELECT sq.id FROM stock_quant sq
                 WHERE sq.id IN %s ORDER BY sq.id FOR UPDATE
                """,
                (candidate_quant_ids,),
            )
        if lot_pairs:
            values_sql = ",".join(["(%s,%s)"] * len(lot_pairs))
            params = []
            for product_id, lot_id in lot_pairs:
                params.extend([product_id, lot_id])
            self.env.cr.execute(
                """
                SELECT ll.id
                  FROM agx_shipment_lot_line ll
                  JOIN agx_shipment s ON s.id = ll.shipment_id
                  JOIN (VALUES {v}) AS scope(product_id, lot_id)
                    ON scope.product_id = ll.product_id
                   AND scope.lot_id = ll.lot_id
                 WHERE s.company_id = %s AND s.state != 'cancelled'
                 ORDER BY ll.product_id, ll.lot_id, ll.id FOR UPDATE
                """.format(v=values_sql),
                tuple(params + [self.company_id.id]),
            )

    def _get_other_reserved_qty(
        self,
        lot_id,
        product_id,
        exclude_shipment_id=False,
        exclude_lot_line_id=False,
    ):
        """Return the sum of reserved_qty for a lot across other shipments.

        Used by ``_get_lot_effective_available_qty`` to detect double-booking.
        """
        domain = [
            ("lot_id", "=", lot_id),
            ("product_id", "=", product_id),
            ("shipment_id.state", "!=", "cancelled"),
        ]
        if exclude_shipment_id:
            domain.append(("shipment_id", "!=", exclude_shipment_id))
        if exclude_lot_line_id:
            domain.append(("id", "!=", exclude_lot_line_id))
        return sum(
            self.env["agx.shipment.lot.line"].search(domain).mapped("reserved_qty")
        )

    def _get_lot_physical_available_qty(self, lot_id, product_id):
        """Return physical available qty (quantity − reserved_quantity) for a lot.

        Reads stock.quant directly so it accounts for reservations made
        by other Odoo operations (not just AGX shipments).
        """
        quants = self.env["stock.quant"].search(
            [
                ("company_id", "=", self.company_id.id),
                ("product_id", "=", product_id),
                ("lot_id", "=", lot_id),
                ("location_id.usage", "=", "internal"),
            ]
        )
        return sum(
            max((q.quantity or 0.0) - (q.reserved_quantity or 0.0), 0.0)
            for q in quants
        )

    def _get_lot_effective_available_qty(
        self,
        lot_id,
        product_id,
        exclude_shipment_id=False,
        exclude_lot_line_id=False,
    ):
        """Return qty available for this lot after all AGX reservations.

        = physical_available − sum(other AGX shipment reservations for this lot)

        This dual-layer check prevents:
          1. Over-reserving beyond physical stock (via stock.quant).
          2. Double-booking the same lot in two AGX shipments simultaneously
             (via agx_shipment_lot_line, before Odoo itself reserves).
        """
        physical = self._get_lot_physical_available_qty(lot_id, product_id)
        other = self._get_other_reserved_qty(
            lot_id=lot_id,
            product_id=product_id,
            exclude_shipment_id=exclude_shipment_id,
            exclude_lot_line_id=exclude_lot_line_id,
        )
        return max(physical - other, 0.0)

    def _get_candidate_reservation_scope(self):
        """Identify lots and quants to reserve for all product lines.

        Iterates shipment product lines FIFO (by in_date on stock.quant)
        and respects grade/size filters.  Returns:
          (candidate_quant_ids, candidate_lot_pairs)
        Raises UserError if any line cannot be fully satisfied.
        """
        self.ensure_one()
        Quant = self.env["stock.quant"]
        BatchOutput = self.env["agx.batch.output"]
        candidate_quant_ids = []
        candidate_pairs = set()
        pending = {}  # (product_id, lot_id) → qty being staged

        for line in self.line_ids:
            needed = line.product_qty
            if needed <= 0:
                continue
            quants = Quant.search(
                [
                    ("company_id", "=", self.company_id.id),
                    ("product_id", "=", line.product_id.id),
                    ("location_id.usage", "=", "internal"),
                    ("lot_id", "!=", False),
                    ("quantity", ">", 0),
                ],
                order="in_date,id",
            )
            for quant in quants:
                pair = (line.product_id.id, quant.lot_id.id)
                avail = (
                    self._get_lot_effective_available_qty(
                        lot_id=quant.lot_id.id,
                        product_id=line.product_id.id,
                        exclude_shipment_id=self.id,
                    )
                    - pending.get(pair, 0.0)
                )
                avail = max(avail, 0.0)
                if avail <= 0:
                    continue
                # Grade / size filter from batch output
                output = BatchOutput.search(
                    [
                        ("lot_id", "=", quant.lot_id.id),
                        ("product_id", "=", line.product_id.id),
                    ],
                    limit=1,
                )
                if line.grade_id and output and output.grade_id != line.grade_id:
                    continue
                if line.size_id and output and output.size_id != line.size_id:
                    continue
                take = min(needed, avail)
                if take <= 0:
                    continue
                candidate_quant_ids.append(quant.id)
                candidate_pairs.add(pair)
                pending[pair] = pending.get(pair, 0.0) + take
                needed -= take
                if needed <= 0:
                    break
            if needed > 0:
                raise UserError(
                    _("Not enough available lots for product %s.")
                    % line.product_id.display_name
                )
        return sorted(set(candidate_quant_ids)), sorted(candidate_pairs)

    def _compute_reservation_allocations_from_candidates(self, candidate_quant_ids):
        """Build lot_line_ids create commands from pre-selected quants.

        Uses the same FIFO ordering and grade/size filters as the
        candidate selection step.
        """
        self.ensure_one()
        BatchOutput = self.env["agx.batch.output"]
        allocations = []
        pending = {}

        candidate_quants = self.env["stock.quant"].search(
            [("id", "in", candidate_quant_ids)], order="in_date,id"
        )
        by_product = {}
        for q in candidate_quants:
            by_product.setdefault(q.product_id.id, []).append(q)

        for line in self.line_ids:
            needed = line.product_qty
            if needed <= 0:
                continue
            for quant in by_product.get(line.product_id.id, []):
                pair = (line.product_id.id, quant.lot_id.id)
                avail = (
                    self._get_lot_effective_available_qty(
                        lot_id=quant.lot_id.id,
                        product_id=line.product_id.id,
                        exclude_shipment_id=self.id,
                    )
                    - pending.get(pair, 0.0)
                )
                avail = max(avail, 0.0)
                if avail <= 0:
                    continue
                output = BatchOutput.search(
                    [
                        ("lot_id", "=", quant.lot_id.id),
                        ("product_id", "=", line.product_id.id),
                    ],
                    limit=1,
                )
                if line.grade_id and output and output.grade_id != line.grade_id:
                    continue
                if line.size_id and output and output.size_id != line.size_id:
                    continue
                take = min(needed, avail)
                if take <= 0:
                    continue
                allocations.append(
                    (
                        0,
                        0,
                        {
                            "shipment_line_id": line.id,
                            "product_id": line.product_id.id,
                            "lot_id": quant.lot_id.id,
                            "batch_output_id": output.id if output else False,
                            "available_qty": avail,
                            "reserved_qty": take,
                        },
                    )
                )
                pending[pair] = pending.get(pair, 0.0) + take
                needed -= take
                if needed <= 0:
                    break
            if needed > 0:
                raise UserError(
                    _("Not enough available lots for product %s.")
                    % line.product_id.display_name
                )
        return allocations

    def _lock_lot_reservation_scope(self, lot_id, product_id):
        """Acquire row locks for a single lot validation (used by constraint)."""
        self.ensure_one()
        if not lot_id or not product_id:
            return
        self.env["stock.quant"].flush_model(
            ["company_id", "product_id", "lot_id",
             "quantity", "reserved_quantity", "location_id"]
        )
        self.env["agx.shipment.lot.line"].flush_model(
            ["shipment_id", "product_id", "lot_id", "reserved_qty"]
        )
        self.env.cr.execute(
            """
            SELECT sq.id FROM stock_quant sq
              JOIN stock_location sl ON sl.id = sq.location_id
             WHERE sq.company_id = %s AND sq.product_id = %s
               AND sq.lot_id = %s AND sl.usage = 'internal'
             ORDER BY sq.id FOR UPDATE
            """,
            (self.company_id.id, product_id, lot_id),
        )
        self.env.cr.execute(
            """
            SELECT ll.id FROM agx_shipment_lot_line ll
              JOIN agx_shipment s ON s.id = ll.shipment_id
             WHERE s.company_id = %s AND ll.product_id = %s
               AND ll.lot_id = %s AND s.state != 'cancelled'
             ORDER BY ll.id FOR UPDATE
            """,
            (self.company_id.id, product_id, lot_id),
        )

    # ------------------------------------------------------------------
    # Delivery picking builders
    # ------------------------------------------------------------------
    def _unreserve_existing_delivery(self):
        """Cleanly release stock.quant reservations on the existing picking.

        Called before rebuilding the delivery to avoid ghost reservations
        in stock.quant.reserved_quantity (double-reservation bug fix).
        """
        for rec in self:
            picking = rec.delivery_picking_id
            if not picking or picking.state in ("done", "cancel"):
                continue
            try:
                picking.do_unreserve()
            except Exception:
                pass
            moves = picking.move_ids.filtered(
                lambda m: m.state not in ("done", "cancel")
            )
            if moves:
                moves._action_cancel()
            picking.message_post(
                body=_("Delivery unreserved and rebuilt from latest AGX reservation.")
            )

    def _build_delivery_picking(self):
        """Create (or rebuild) the outgoing delivery picking.

        Steps:
          1. Unreserve and cancel existing pending delivery moves
             (fixes double-reservation in stock.quant).
          2. Create a new stock.picking with agx_flow_type = 'shipment'.
          3. Create stock.move for each product line.
          4. Confirm the picking (moves to 'confirmed' state).
             NOTE: action_assign() is intentionally NOT called here.
             Move lines with specific lots are created manually in
             action_ship(), which gives precise lot-level control.
        """
        Move = self.env["stock.move"]
        for rec in self:
            source, customer, out_type = rec._validate_delivery_settings()
            rec._unreserve_existing_delivery()

            picking_vals = {
                "picking_type_id": out_type.id,
                "location_id": source.id,
                "location_dest_id": customer.id,
                "partner_id": rec.customer_id.id if rec.customer_id else False,
                "origin": rec.name,
                "company_id": rec.company_id.id,
                "agx_shipment_id": rec.id,
                "agx_flow_type": "shipment",
            }
            # Stamp season analytic account for P&L posting
            if rec.season_id and rec.season_id.analytic_account_id:
                picking_vals["analytic_account_id"] = (
                    rec.season_id.analytic_account_id.id
                )
            picking = self.env["stock.picking"].create(picking_vals)

            for line in rec.line_ids.filtered(
                lambda l: l.product_id and l.product_qty > 0
            ):
                Move.create(
                    {
                        "description_picking": "{} / Delivery / {}".format(
                            rec.name, line.product_id.display_name
                        ),
                        "company_id": rec.company_id.id,
                        "product_id": line.product_id.id,
                        "product_uom_qty": line.product_qty,
                        "product_uom": (
                            line.uom_id or line.product_id.uom_id
                        ).id,
                        "location_id": source.id,
                        "location_dest_id": customer.id,
                        "agx_shipment_id": rec.id,
                        "agx_shipment_line_id": line.id,
                        "agx_flow_type": "shipment",
                        "origin": rec.name,
                        "picking_id": picking.id,
                    }
                )
            picking.action_confirm()
            # Do NOT call action_assign() — move lines are built manually
            # in action_ship() to achieve exact lot-level control.
            rec.delivery_picking_id = picking.id

    # ------------------------------------------------------------------
    # State actions
    # ------------------------------------------------------------------
    def action_reserve(self):
        """Reserve lots for this shipment.

        Workflow:
          1. Ensure containers and line-container assignments are correct.
          2. Identify candidate lots (FIFO, grade/size filtered).
          3. Acquire DB locks to prevent race conditions.
          4. Delete old lot_line_ids (unreserving quant reservations first).
          5. Write new lot_line_ids.
          6. Build delivery picking (confirmed, not assigned — intentional).
          7. Set state to 'reserved'.
        """
        for rec in self:
            rec._sync_container_setup()
            rec._normalize_line_containers()
            candidates, pairs = rec._get_candidate_reservation_scope()
            rec._lock_reservation_scope(candidates, pairs)
            allocations = rec._compute_reservation_allocations_from_candidates(
                candidates
            )
            # Unreserve existing delivery BEFORE deleting lot lines
            # so stock.quant.reserved_quantity is properly decremented.
            rec._unreserve_existing_delivery()
            rec.lot_line_ids.unlink()
            if allocations:
                rec.write({"lot_line_ids": allocations})
            rec._build_delivery_picking()
            rec.state = "reserved"
        return True

    def action_ship(self):
        """Validate the shipment — move goods from stock to customer.

        Workflow:
          1. Verify lot lines exist.
          2. Re-build delivery picking if it was cancelled.
          3. For each move, delete any auto-assigned move lines and
             replace them with lines built from lot_line_ids.
             This gives exact lot-level control over what ships.
          4. Validate the picking (button_validate).
          5. Set state to 'shipped'.

        NOTE: action_assign() is deliberately NOT called.  Calling it
        on a picking that already has move lines raises "Nothing to check
        availability for" in Odoo 19.
        """
        MoveLine = self.env["stock.move.line"]
        for rec in self:
            if not rec.lot_line_ids:
                raise UserError(_("Please reserve lots before shipping."))
            if not rec.delivery_picking_id or rec.delivery_picking_id.state in (
                "cancel",
            ):
                rec._build_delivery_picking()
            picking = rec.delivery_picking_id
            if picking.state == "draft":
                picking.action_confirm()

            qty_field = rec._quantity_field_name(MoveLine)
            src = picking.location_id
            dst = picking.location_dest_id

            for move in picking.move_ids.filtered(
                lambda m: m.state not in ("done", "cancel")
            ):
                move.move_line_ids.unlink()
                reserved = rec.lot_line_ids.filtered(
                    lambda l: l.shipment_line_id
                    and l.shipment_line_id == move.agx_shipment_line_id
                )
                if not reserved:
                    continue
                move.product_uom_qty = sum(reserved.mapped("reserved_qty"))
                for ll in reserved:
                    vals = {
                        "move_id": move.id,
                        "product_id": move.product_id.id,
                        "product_uom_id": move.product_uom.id,
                        "location_id": src.id,
                        "location_dest_id": dst.id,
                        "lot_id": ll.lot_id.id,
                    }
                    vals[qty_field] = ll.reserved_qty
                    MoveLine.create(vals)

            res = picking.with_context(
                skip_immediate=True, skip_backorder=True
            ).button_validate()
            if isinstance(res, dict):
                pending = picking.move_ids.filtered(
                    lambda m: m.state not in ("done", "cancel")
                )
                if pending:
                    pending._action_done()
            rec.state = "shipped"
        return True

    def action_cancel(self):
        """Cancel shipment and unreserve any pending delivery."""
        for rec in self:
            rec._unreserve_existing_delivery()
        self.write({"state": "cancelled"})

    def action_create_sale_order(self):
        """Create a Sales Order linked to this shipment.

        One SO line per shipment (priced at 0 — user sets price).
        The SO links back the revenue figure for profitability calculation.
        """
        self.ensure_one()
        if not self.customer_id:
            raise UserError(
                _("Please select a customer before creating the Sales Order.")
            )
        if self.sale_order_id:
            return self.action_view_sale_order()
        product = (
            self.company_id.agx_container_service_product_id
            or self.env["product.product"].search(
                [("sale_ok", "=", True)], limit=1
            )
        )
        if not product:
            raise UserError(
                _("Please configure a container service product in Settings.")
            )
        so_vals = {
            "partner_id": self.customer_id.id,
            "company_id": self.company_id.id,
            "origin": self.name,
            "order_line": [
                (
                    0,
                    0,
                    {
                        "product_id": product.id,
                        "name": "{} {}".format(
                            self.company_id.agx_so_line_prefix or "Shipment",
                            self.name,
                        ),
                        "product_uom_qty": (
                            self.container_count or len(self.container_ids) or 1
                        ),
                        "price_unit": 0.0,
                    },
                )
            ],
        }
        # Link analytic account from season for revenue tracking
        if self.season_id and self.season_id.analytic_account_id:
            so_vals["analytic_account_id"] = (
                self.season_id.analytic_account_id.id
            )
        so = self.env["sale.order"].create(so_vals)
        self.sale_order_id = so.id
        return self.action_view_sale_order()

    def action_view_sale_order(self):
        self.ensure_one()
        if not self.sale_order_id:
            return False
        return {
            "type": "ir.actions.act_window",
            "name": _("Sales Order"),
            "res_model": "sale.order",
            "res_id": self.sale_order_id.id,
            "view_mode": "form",
            "target": "current",
        }

    def action_view_delivery(self):
        self.ensure_one()
        if not self.delivery_picking_id:
            return False
        return {
            "type": "ir.actions.act_window",
            "name": _("Delivery"),
            "res_model": "stock.picking",
            "res_id": self.delivery_picking_id.id,
            "view_mode": "form",
            "target": "current",
        }


# ---------------------------------------------------------------------------
# Shipment Container
# ---------------------------------------------------------------------------
class AgxShipmentContainer(models.Model):
    """Individual container within a shipment.

    The header ``container_no`` and ``seal_no`` always reflect the first
    container (synced bidirectionally).  Additional containers have their
    own numbers entered directly on the Containers tab.
    """

    _name = "agx.shipment.container"
    _description = "Shipment Container"
    _order = "sequence, id"

    sequence = fields.Integer(default=10)
    shipment_id = fields.Many2one(
        "agx.shipment", required=True, ondelete="cascade"
    )
    name = fields.Char(help="Auto-generated container label, e.g. SHP/001-C1.")
    container_no = fields.Char()
    seal_no = fields.Char()
    note = fields.Char()
    line_ids = fields.One2many(
        "agx.shipment.line", "container_id", string="Container Product Lines"
    )

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        for rec in records:
            if not rec.name:
                rec.name = "{}-C{}".format(
                    rec.shipment_id.name, len(rec.shipment_id.container_ids)
                )
            if rec.shipment_id and rec.shipment_id.container_count != len(
                rec.shipment_id.container_ids
            ):
                rec.shipment_id.with_context(
                    agx_skip_container_sync=True
                ).write({"container_count": len(rec.shipment_id.container_ids)})
        return records

    def write(self, vals):
        res = super().write(vals)
        # Keep header in sync when first container is edited
        for rec in self:
            first = (
                rec.shipment_id.container_ids.sorted("sequence")[:1]
                if rec.shipment_id
                else False
            )
            if first and rec.id == first.id:
                rec.shipment_id.with_context(
                    agx_skip_container_sync=True
                ).write(
                    {
                        "container_no": rec.container_no,
                        "seal_no": rec.seal_no,
                    }
                )
        return res


# ---------------------------------------------------------------------------
# Shipment Line
# ---------------------------------------------------------------------------
class AgxShipmentLine(models.Model):
    """One product line within a shipment (linked to a container).

    Allocated logistics cost is computed from the shipment's cost lines
    using the specified allocation basis (qty / carton / weight / equal).
    Denominators are pre-aggregated per shipment to avoid O(n²) queries.
    """

    _name = "agx.shipment.line"
    _description = "Shipment Product Line"
    _order = "sequence, id"

    sequence = fields.Integer(default=10)
    shipment_id = fields.Many2one(
        "agx.shipment", required=True, ondelete="cascade"
    )
    container_id = fields.Many2one(
        "agx.shipment.container", string="Container"
    )
    company_id = fields.Many2one(
        related="shipment_id.company_id", store=True, readonly=True
    )
    currency_id = fields.Many2one(
        related="shipment_id.currency_id", store=True, readonly=True
    )
    product_id = fields.Many2one("product.product", required=True)
    grade_id = fields.Many2one("agx.grade")
    size_id = fields.Many2one("agx.size")
    product_qty = fields.Float(required=True, default=1.0)
    uom_id = fields.Many2one("uom.uom")
    carton_qty = fields.Float(default=0.0)
    net_weight = fields.Float(default=0.0)
    gross_weight = fields.Float(default=0.0)
    allocated_logistics_cost = fields.Monetary(
        currency_field="currency_id",
        compute="_compute_allocated_costs",
        store=True,
    )
    cost_per_carton = fields.Monetary(
        currency_field="currency_id",
        compute="_compute_allocated_costs",
        store=True,
    )
    cost_per_qty = fields.Monetary(
        currency_field="currency_id",
        compute="_compute_allocated_costs",
        store=True,
    )

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        for rec in records:
            if not rec.container_id and rec.shipment_id.container_ids:
                first = rec.shipment_id.container_ids.sorted("sequence")[:1]
                if first:
                    rec.container_id = first.id
        return records

    @api.onchange("product_id")
    def _onchange_product_id(self):
        """Default UoM and auto-fill grade/size from variant attributes."""
        for rec in self:
            if rec.product_id:
                rec.uom_id = rec.product_id.uom_id
                # Auto-fill grade/size from variant attribute values
                for ptav in rec.product_id.product_template_attribute_value_ids:
                    av = ptav.product_attribute_value_id
                    grade = self.env["agx.grade"].search(
                        [("attribute_value_id", "=", av.id)], limit=1
                    )
                    if grade:
                        rec.grade_id = grade
                    size = self.env["agx.size"].search(
                        [("attribute_value_id", "=", av.id)], limit=1
                    )
                    if size:
                        rec.size_id = size
            if not rec.container_id and rec.shipment_id.container_ids:
                rec.container_id = rec.shipment_id.container_ids.sorted(
                    "sequence"
                )[:1]

    @api.constrains("container_id", "shipment_id")
    def _check_container_shipment(self):
        for rec in self:
            if (
                rec.container_id
                and rec.container_id.shipment_id != rec.shipment_id
            ):
                raise ValidationError(
                    _("The selected container must belong to the same shipment.")
                )

    @api.depends(
        "shipment_id.cost_line_ids.effective_amount",
        "shipment_id.cost_line_ids.allocation_basis",
        "shipment_id.line_ids.product_qty",
        "shipment_id.line_ids.carton_qty",
        "shipment_id.line_ids.net_weight",
        "shipment_id.line_ids.gross_weight",
    )
    def _compute_allocated_costs(self):
        """Allocate logistics costs to this line based on the cost basis.

        Pre-aggregates denominators per shipment (once) so the inner loop
        only does arithmetic — no additional ORM queries per cost line.
        """
        denoms_cache = {}
        for line in self:
            sid = line.shipment_id.id
            if sid not in denoms_cache:
                lines = line.shipment_id.line_ids
                denoms_cache[sid] = {
                    "qty": sum(lines.mapped("product_qty")),
                    "carton": sum(lines.mapped("carton_qty")),
                    "net_weight": sum(lines.mapped("net_weight")),
                    "gross_weight": sum(lines.mapped("gross_weight")),
                    "count": len(lines),
                }
            d = denoms_cache[sid]
            total = 0.0
            for cl in line.shipment_id.cost_line_ids:
                basis = cl.allocation_basis
                if basis == "carton":
                    denom, num = d["carton"], line.carton_qty
                elif basis == "net_weight":
                    denom, num = d["net_weight"], line.net_weight
                elif basis == "gross_weight":
                    denom, num = d["gross_weight"], line.gross_weight
                elif basis == "equal":
                    denom, num = d["count"], 1.0 if line.id else 0.0
                else:
                    denom, num = d["qty"], line.product_qty
                if denom:
                    total += cl.effective_amount * (num / denom)
            line.allocated_logistics_cost = total
            line.cost_per_carton = total / line.carton_qty if line.carton_qty else 0.0
            line.cost_per_qty = total / line.product_qty if line.product_qty else 0.0


# ---------------------------------------------------------------------------
# Shipment Lot Line
# ---------------------------------------------------------------------------
class AgxShipmentLotLine(models.Model):
    """Reserved lot for a shipment product line.

    Created automatically by ``action_reserve`` or manually via the
    Reserve Lots Wizard.  Validated by a constraint that prevents
    over-reservation beyond the lot's effective available quantity.
    """

    _name = "agx.shipment.lot.line"
    _description = "Reserved Lot Line"
    _order = "sequence, id"

    sequence = fields.Integer(default=10)
    shipment_id = fields.Many2one(
        "agx.shipment", required=True, ondelete="cascade"
    )
    shipment_line_id = fields.Many2one("agx.shipment.line")
    container_id = fields.Many2one(
        "agx.shipment.container",
        related="shipment_line_id.container_id",
        store=True,
        readonly=True,
    )
    product_id = fields.Many2one("product.product", required=True)
    lot_id = fields.Many2one("stock.lot", required=True)
    batch_output_id = fields.Many2one(
        "agx.batch.output",
        help="Batch output that produced this lot (for traceability).",
    )
    available_qty = fields.Float(
        help="Effective available qty at reservation time (informational)."
    )
    reserved_qty = fields.Float(required=True, default=1.0)
    note = fields.Char()

    @api.constrains(
        "reserved_qty", "available_qty", "lot_id", "product_id", "shipment_id"
    )
    def _check_reserved_qty(self):
        """Prevent over-reservation and zero/negative quantities."""
        for rec in self:
            if rec.reserved_qty <= 0:
                raise ValidationError(
                    _("Reserved quantity must be greater than zero.")
                )
            if rec.shipment_id:
                rec.shipment_id._lock_lot_reservation_scope(
                    lot_id=rec.lot_id.id, product_id=rec.product_id.id
                )
            effective = rec.shipment_id._get_lot_effective_available_qty(
                lot_id=rec.lot_id.id,
                product_id=rec.product_id.id,
                exclude_lot_line_id=rec.id,
            )
            if rec.reserved_qty > effective:
                raise ValidationError(
                    _(
                        "Reserved quantity (%(qty)s) exceeds the available "
                        "quantity for this lot (%(avail)s)."
                    )
                    % {"qty": rec.reserved_qty, "avail": effective}
                )


# ---------------------------------------------------------------------------
# Shipment Cost Line
# ---------------------------------------------------------------------------
class AgxShipmentCostLine(models.Model):
    """One logistics cost item on a shipment.

    Supports three source modes:
      manual          — amount entered by hand
      vendor_bill     — total of a linked vendor bill
      vendor_bill_line— amount from a specific bill line
      landed_cost     — total of a linked landed cost

    The ``effective_amount`` is always used for cost allocation, regardless
    of source — simplifying the allocation compute logic.
    """

    _name = "agx.shipment.cost.line"
    _description = "Shipment Cost Line"
    _order = "sequence, id"

    sequence = fields.Integer(default=10)
    shipment_id = fields.Many2one(
        "agx.shipment", required=True, ondelete="cascade"
    )
    company_id = fields.Many2one(
        related="shipment_id.company_id", store=True, readonly=True
    )
    currency_id = fields.Many2one(
        related="shipment_id.currency_id", store=True, readonly=True
    )
    name = fields.Char()
    cost_type_id = fields.Many2one("agx.shipment.cost.type", required=True)
    allocation_basis = fields.Selection(
        [
            ("qty", "By Quantity"),
            ("carton", "By Cartons"),
            ("net_weight", "By Net Weight"),
            ("gross_weight", "By Gross Weight"),
            ("equal", "Equal Share"),
        ],
        default="qty",
        required=True,
    )
    cost_source = fields.Selection(
        [
            ("manual", "Manual"),
            ("vendor_bill", "Vendor Bill"),
            ("vendor_bill_line", "Vendor Bill Line"),
            ("landed_cost", "Landed Cost"),
        ],
        default="manual",
        required=True,
    )
    manual_amount = fields.Monetary(currency_field="currency_id", default=0.0)
    source_amount = fields.Monetary(
        currency_field="currency_id",
        compute="_compute_source_amount",
        store=True,
    )
    effective_amount = fields.Monetary(
        currency_field="currency_id",
        compute="_compute_source_amount",
        store=True,
        help="The amount used for cost allocation (manual or sourced).",
    )
    vendor_bill_id = fields.Many2one(
        "account.move", domain="[('move_type', '=', 'in_invoice')]"
    )
    vendor_bill_line_id = fields.Many2one("account.move.line")
    landed_cost_id = fields.Many2one("stock.landed.cost")
    note = fields.Char()

    @api.depends(
        "cost_source",
        "manual_amount",
        "vendor_bill_id.amount_untaxed",
        "vendor_bill_line_id.price_subtotal",
        "landed_cost_id.amount_total",
    )
    def _compute_source_amount(self):
        """Derive source_amount and effective_amount from the linked document."""
        for rec in self:
            src = 0.0
            if rec.cost_source == "vendor_bill_line" and rec.vendor_bill_line_id:
                src = rec.vendor_bill_line_id.price_subtotal
            elif rec.cost_source == "vendor_bill" and rec.vendor_bill_id:
                src = rec.vendor_bill_id.amount_untaxed
            elif rec.cost_source == "landed_cost" and rec.landed_cost_id:
                src = rec.landed_cost_id.amount_total
            rec.source_amount = src
            rec.effective_amount = (
                src if rec.cost_source != "manual" else rec.manual_amount
            )
