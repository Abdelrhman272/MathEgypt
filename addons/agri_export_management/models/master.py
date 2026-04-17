# -*- coding: utf-8 -*-
"""
master.py — Master Data Models for Agricultural Export Management
=================================================================
Contains all reference / configuration models:
  - AgxFarm            : farms that supply raw produce
  - AgxCrop            : crop types / varieties
  - AgxGrade           : quality grades (A, B, C …) — linked to product variants
  - AgxSize            : pack sizes (36, 38, 40 …)  — linked to product variants
  - AgxSeason          : production seasons (multiple may run simultaneously)
  - AgxDestination     : export destinations / ports
  - AgxShipmentCostType: logistics cost categories

Design notes
------------
* AgxGrade and AgxSize each carry ``attribute_value_id`` pointing to
  the matching ``product.attribute.value``.  This bridges AGX grades/sizes
  to Odoo product variants so each Grade+Size combination is a distinct
  product tracked separately in stock.

* AgxSeason owns an ``account.analytic.account`` created automatically
  on save.  Every cost line (PO, batch, shipment) posts against this
  analytic account, enabling a full P&L per season from Odoo's built-in
  analytic reports without extra configuration.
"""

from odoo import _, api, fields, models


# ---------------------------------------------------------------------------
# Farm
# ---------------------------------------------------------------------------
class AgxFarm(models.Model):
    """Represents a supplier farm.

    Linked to a ``res.partner`` (the owner/vendor) so purchase orders
    can be raised directly from the evaluation without re-entering
    contact details.
    """

    _name = "agx.farm"
    _description = "Farm"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "name"

    name = fields.Char(required=True, tracking=True)
    code = fields.Char(copy=False)
    partner_id = fields.Many2one(
        "res.partner",
        string="Owner / Vendor",
        tracking=True,
        help="Vendor used when creating purchase orders from an evaluation.",
    )
    company_id = fields.Many2one(
        "res.company",
        required=True,
        default=lambda self: self.env.company,
        index=True,
    )
    region = fields.Char(
        help="Geographic region (e.g. Delta, Upper Egypt)."
    )
    location = fields.Char(help="Specific location / address notes.")
    active = fields.Boolean(default=True)
    note = fields.Html()


# ---------------------------------------------------------------------------
# Crop / Variety
# ---------------------------------------------------------------------------
class AgxCrop(models.Model):
    """Crop or variety (e.g. Navel Orange, Valencia Orange).

    Selected on the evaluation and used as a grouping dimension for
    season-level reporting.
    """

    _name = "agx.crop"
    _description = "Crop / Variety"
    _order = "name"

    name = fields.Char(required=True)
    code = fields.Char()
    active = fields.Boolean(default=True)
    note = fields.Text()


# ---------------------------------------------------------------------------
# Grade
# ---------------------------------------------------------------------------
class AgxGrade(models.Model):
    """Quality grade assigned to finished-goods output (e.g. A, B, Export).

    The ``attribute_value_id`` field links this grade to a
    ``product.attribute.value`` on the **Grade** product attribute.
    When the *Initialize Product Attributes* wizard runs, Odoo creates
    the corresponding product variant automatically — making the grade
    visible in stock reports per variant.

    Workflow:
      1. User defines grades here (A, B, C …).
      2. Admin runs Configuration > Initialize Product Attributes.
      3. Wizard creates / links the attribute values and generates variants
         on all finished-goods products that have the Grade attribute.
    """

    _name = "agx.grade"
    _description = "Grade"
    _order = "sequence, name"

    name = fields.Char(required=True)
    code = fields.Char()
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    note = fields.Text()

    # ------------------------------------------------------------------
    # Product Variant bridge
    # ------------------------------------------------------------------
    attribute_value_id = fields.Many2one(
        "product.attribute.value",
        string="Product Attribute Value",
        copy=False,
        help=(
            "Links this grade to the matching product.attribute.value so "
            "finished-goods variants are tracked individually in stock. "
            "Set automatically by the Initialize Product Attributes wizard."
        ),
    )


# ---------------------------------------------------------------------------
# Size
# ---------------------------------------------------------------------------
class AgxSize(models.Model):
    """Pack size — number of fruit per carton (e.g. 36, 40, 48).

    Like AgxGrade, carries ``attribute_value_id`` bridging the AGX size
    to a ``product.attribute.value`` on the **Size** attribute, enabling
    per-variant stock tracking (e.g. 'Orange / Grade A / Size 40').
    """

    _name = "agx.size"
    _description = "Size"
    _order = "sequence, number, id"

    name = fields.Char(
        compute="_compute_name",
        store=True,
        help="Auto-computed from size number (e.g. '36', '40').",
    )
    number = fields.Integer(required=True, help="Number of fruit per carton.")
    code = fields.Char()
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    note = fields.Text()

    # ------------------------------------------------------------------
    # Product Variant bridge
    # ------------------------------------------------------------------
    attribute_value_id = fields.Many2one(
        "product.attribute.value",
        string="Product Attribute Value",
        copy=False,
        help=(
            "Links this size to the matching product.attribute.value so "
            "finished-goods variants are tracked individually in stock. "
            "Set automatically by the Initialize Product Attributes wizard."
        ),
    )

    @api.depends("number")
    def _compute_name(self):
        """Derive display name from the numeric size value."""
        for rec in self:
            rec.name = str(rec.number) if rec.number else "0"


# ---------------------------------------------------------------------------
# Season
# ---------------------------------------------------------------------------
class AgxSeason(models.Model):
    """Production season — primary analytic / reporting dimension.

    A season groups all evaluations, batches, and shipments belonging
    to one harvest cycle for a given crop.  Multiple seasons can run
    simultaneously (e.g. Orange Season 2025 and Grape Season 2025).

    Analytic accounting
    -------------------
    On creation, an ``account.analytic.account`` is created and stored
    in ``analytic_account_id``.  All cost-bearing documents post against
    this account so a full P&L per season is available in Odoo's
    analytic reports with no extra configuration.

    Example season P&L flow:
      PO cost → analytic line on season account
      Batch production cost → analytic line on season account
      Shipment logistics cost → analytic line on season account
      SO revenue → analytic line on season account
      ─────────────────────────────────────────
      Net → Gross Margin per Season
    """

    _name = "agx.season"
    _description = "Production Season"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "date_start desc, name"

    name = fields.Char(
        required=True,
        tracking=True,
        help="Human-readable name, e.g. 'Orange Season 2025'.",
    )
    code = fields.Char(
        copy=False,
        help="Short code used as sequence prefix, e.g. 'ORG-25'.",
    )
    crop_id = fields.Many2one(
        "agx.crop",
        string="Crop",
        tracking=True,
    )
    company_id = fields.Many2one(
        "res.company",
        required=True,
        default=lambda self: self.env.company,
        index=True,
    )
    date_start = fields.Date(string="Start Date", required=True, tracking=True)
    date_end = fields.Date(string="End Date", tracking=True)
    state = fields.Selection(
        [
            ("draft", "Draft"),
            ("active", "Active"),
            ("closed", "Closed"),
        ],
        default="draft",
        tracking=True,
    )
    note = fields.Html()

    # ------------------------------------------------------------------
    # Analytic accounting bridge
    # ------------------------------------------------------------------
    analytic_account_id = fields.Many2one(
        "account.analytic.account",
        string="Analytic Account",
        copy=False,
        readonly=True,
        help=(
            "Auto-created analytic account. All costs and revenues linked "
            "to this season post here for a full per-season P&L."
        ),
    )

    # ------------------------------------------------------------------
    # Stat button counts
    # ------------------------------------------------------------------
    evaluation_count = fields.Integer(compute="_compute_counts")
    batch_count = fields.Integer(compute="_compute_counts")
    shipment_count = fields.Integer(compute="_compute_counts")

    # ------------------------------------------------------------------
    # ORM overrides
    # ------------------------------------------------------------------
    @api.model_create_multi
    def create(self, vals_list):
        """Create matching analytic account on season creation."""
        records = super().create(vals_list)
        for rec in records:
            rec._ensure_analytic_account()
        return records

    def write(self, vals):
        """Keep analytic account name in sync when season name changes."""
        res = super().write(vals)
        if "name" in vals or "crop_id" in vals:
            for rec in self:
                if rec.analytic_account_id:
                    rec.analytic_account_id.name = rec._analytic_account_name()
        return res

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _analytic_account_name(self):
        """Return a descriptive name for the linked analytic account."""
        self.ensure_one()
        crop = self.crop_id.name if self.crop_id else _("Season")
        return "{} — {}".format(crop, self.name)

    def _get_default_analytic_plan(self):
        """Return the default analytic plan id (required Odoo 17+).

        Finds or creates a plan named 'AGX Seasons'.
        Returns False for Odoo versions without analytic plans.

        Note: account.analytic.plan in Odoo 19 has no company_id field —
        plans are global (not company-specific).
        """
        Plan = self.env.get("account.analytic.plan")
        if Plan is None:
            return False
        # Odoo 19: account.analytic.plan has no company_id — search by name only
        plan = Plan.search([("name", "=", "AGX Seasons")], limit=1)
        if not plan:
            plan = Plan.create({"name": "AGX Seasons"})
        return plan.id

    def _ensure_analytic_account(self):
        """Create an analytic account for this season if none exists yet.

        Handles both Odoo 17/18 (with plan_id required) and Odoo 19
        (plan_id optional, company_id may or may not exist on the model).
        """
        self.ensure_one()
        if self.analytic_account_id:
            return
        create_vals = {"name": self._analytic_account_name()}

        # Add company_id only if the field exists on account.analytic.account
        AnalyticAccount = self.env["account.analytic.account"]
        if "company_id" in AnalyticAccount._fields:
            create_vals["company_id"] = self.company_id.id

        plan_id = self._get_default_analytic_plan()
        if plan_id:
            create_vals["plan_id"] = plan_id

        account = AnalyticAccount.create(create_vals)
        self.sudo().analytic_account_id = account.id

    @api.depends("name")
    def _compute_counts(self):
        """Count linked documents for stat buttons."""
        Eval = self.env["agx.evaluation"]
        Batch = self.env["agx.batch"]
        Ship = self.env["agx.shipment"]
        for rec in self:
            rec.evaluation_count = Eval.search_count(
                [("season_id", "=", rec.id)]
            )
            rec.batch_count = Batch.search_count(
                [("season_id", "=", rec.id)]
            )
            rec.shipment_count = Ship.search_count(
                [("season_id", "=", rec.id)]
            )

    # ------------------------------------------------------------------
    # State transitions
    # ------------------------------------------------------------------
    def action_activate(self):
        """Mark season as active — ready to receive operations."""
        self.write({"state": "active"})

    def action_close(self):
        """Close season — no further operations should be linked."""
        self.write({"state": "closed"})

    def action_reset_draft(self):
        self.write({"state": "draft"})

    # ------------------------------------------------------------------
    # Smart button navigation
    # ------------------------------------------------------------------
    def action_view_evaluations(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Farm Evaluations"),
            "res_model": "agx.evaluation",
            "view_mode": "list,form",
            "domain": [("season_id", "=", self.id)],
        }

    def action_view_batches(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Production Batches"),
            "res_model": "agx.batch",
            "view_mode": "list,form",
            "domain": [("season_id", "=", self.id)],
        }

    def action_view_shipments(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Export Shipments"),
            "res_model": "agx.shipment",
            "view_mode": "list,form",
            "domain": [("season_id", "=", self.id)],
        }


# ---------------------------------------------------------------------------
# Destination
# ---------------------------------------------------------------------------
class AgxDestination(models.Model):
    """Export destination — country + port combination.

    Used on shipment headers for top-destination reporting.
    """

    _name = "agx.destination"
    _description = "Destination"
    _order = "name"

    name = fields.Char(required=True)
    country_id = fields.Many2one("res.country")
    port_name = fields.Char(help="Loading or discharge port name.")
    active = fields.Boolean(default=True)
    note = fields.Text()


# ---------------------------------------------------------------------------
# Shipment Cost Type
# ---------------------------------------------------------------------------
class AgxShipmentCostType(models.Model):
    """Category for logistics costs on a shipment.

    Examples: Inland Transport, Port Charges, Ocean Freight, Customs.

    ``default_allocation_basis`` drives how costs of this type are
    spread across shipment product lines when no explicit basis is set.
    """

    _name = "agx.shipment.cost.type"
    _description = "Shipment Cost Type"
    _order = "sequence, name"

    name = fields.Char(required=True)
    code = fields.Selection(
        [
            ("inland", "Inland Transport"),
            ("port", "Port Charges"),
            ("ocean", "Ocean Freight"),
            ("customs", "Customs Clearance"),
            ("other", "Other"),
        ],
        required=True,
        default="other",
    )
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    default_allocation_basis = fields.Selection(
        [
            ("qty", "By Quantity"),
            ("carton", "By Cartons"),
            ("net_weight", "By Net Weight"),
            ("gross_weight", "By Gross Weight"),
            ("equal", "Equal Share"),
        ],
        default="qty",
        required=True,
        help="Default cost spread method for this cost type.",
    )
    note = fields.Text()
