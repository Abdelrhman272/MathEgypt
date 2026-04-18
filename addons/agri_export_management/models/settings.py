# -*- coding: utf-8 -*-
"""
settings.py — Company-level configuration for Agricultural Export Management
=============================================================================
Extends ``res.company`` with all AGX-specific settings so that each
company in a multi-company environment can have its own configuration.

Stock location map
------------------
The module uses four named locations that must be configured once:

  raw_material_location_id   : where purchased raw produce lands
                               (incoming receipts destination)
  production_location_id     : virtual production area — MUST have
                               ``usage = 'production'`` in Odoo so the
                               system auto-zeros it when a batch posts
  cold_storage_location_id   : refrigerated storage after packing
                               (finished goods → cold storage → shipment)
  finished_goods_location_id : non-refrigerated finished goods store
                               (fallback if cold storage not configured)

Picking type map
----------------
  internal_picking_type_id   : used for batch consume & output moves
  outgoing_picking_type_id   : used for shipment delivery orders
"""

from odoo import fields, models


class ResCompany(models.Model):
    """Adds AGX configuration fields to the company record."""

    _inherit = "res.company"

    # ------------------------------------------------------------------
    # Sales / Shipment settings
    # ------------------------------------------------------------------
    agx_container_service_product_id = fields.Many2one(
        "product.product",
        string="Container Service Product",
        help=(
            "Default service product placed on the auto-created Sales Order "
            "when a shipment is confirmed (one line per container)."
        ),
    )
    agx_so_line_prefix = fields.Char(
        default="Shipment",
        help="Prefix text used in the Sales Order line description.",
    )

    # ------------------------------------------------------------------
    # Costing settings
    # ------------------------------------------------------------------
    agx_default_logistics_basis = fields.Selection(
        [
            ("qty", "By Quantity"),
            ("carton", "By Cartons"),
            ("net_weight", "By Net Weight"),
            ("gross_weight", "By Gross Weight"),
            ("equal", "Equal Share"),
        ],
        default="qty",
        help="Default method for allocating logistics costs across shipment lines.",
    )
    agx_allow_vendor_bill_cost_source = fields.Boolean(
        default=True,
        help="Allow importing costs from Vendor Bills on shipments.",
    )
    agx_allow_landed_cost_source = fields.Boolean(
        default=True,
        help="Allow importing costs from Landed Costs on shipments.",
    )
    agx_margin_precision = fields.Integer(
        default=2,
        help="Decimal precision used for margin % display.",
    )

    # ------------------------------------------------------------------
    # Lot / Serial settings
    # ------------------------------------------------------------------
    # MRP integration
    agx_use_mrp_production = fields.Boolean(
        string="Use Manufacturing Orders (MRP) instead of Production Batches",
        default=False,
        help=(
            "When ON:  Production Batches menu is hidden.  "
            "Manufacturing menu (MRP) is shown instead.  "
            "When OFF: Production Batches menu is visible.  "
            "MRP menu visibility is controlled by Odoo's own MRP module. "
            "IMPORTANT: Existing batch records are never deleted — "
            "they remain in the database.  Only the menu visibility changes."
        ),
    )

    agx_auto_generate_lot_numbers = fields.Boolean(
        default=True,
        help=(
            "When enabled, batch output lines without a lot number get "
            "an auto-generated one from the stock.lot.serial sequence "
            "when the batch is marked Done."
        ),
    )

    # ------------------------------------------------------------------
    # Stock location settings
    # ------------------------------------------------------------------
    agx_raw_material_location_id = fields.Many2one(
        "stock.location",
        string="Raw Material Location",
        help=(
            "Where purchased raw produce is stored after receipt. "
            "Used as the fallback source location for batch inputs."
        ),
    )
    agx_production_location_id = fields.Many2one(
        "stock.location",
        string="Production Location",
        help=(
            "Virtual production area. IMPORTANT: this location must have "
            "usage = 'Production' in Odoo so that raw-material consumption "
            "moves auto-zero it — no residual raw stock will accumulate here."
        ),
    )
    agx_finished_goods_location_id = fields.Many2one(
        "stock.location",
        string="Finished Goods Location",
        help=(
            "Where packed finished goods land after the batch output move. "
            "Used as the source for shipment delivery orders when cold "
            "storage is not configured."
        ),
    )
    agx_cold_storage_location_id = fields.Many2one(
        "stock.location",
        string="Cold Storage Location",
        help=(
            "Refrigerated storage location. When set, the batch output move "
            "goes to Finished Goods first; a separate 'Move to Cold Storage' "
            "transfer can then be triggered from the batch to move goods here. "
            "Shipment deliveries pick from Cold Storage when available."
        ),
    )

    # ------------------------------------------------------------------
    # Picking type settings
    # ------------------------------------------------------------------
    agx_internal_picking_type_id = fields.Many2one(
        "stock.picking.type",
        string="Internal Transfer Type",
        help="Picking type used for batch consumption and output transfers.",
    )
    agx_outgoing_picking_type_id = fields.Many2one(
        "stock.picking.type",
        string="Outgoing Delivery Type",
        help="Picking type used when creating shipment delivery orders.",
    )


class ResConfigSettings(models.TransientModel):
    """Exposes AGX company fields in the standard Settings UI."""

    _inherit = "res.config.settings"

    # Sales / Shipment
    agx_container_service_product_id = fields.Many2one(
        related="company_id.agx_container_service_product_id", readonly=False
    )
    agx_so_line_prefix = fields.Char(
        related="company_id.agx_so_line_prefix", readonly=False
    )

    # Costing
    agx_default_logistics_basis = fields.Selection(
        related="company_id.agx_default_logistics_basis", readonly=False
    )
    agx_allow_vendor_bill_cost_source = fields.Boolean(
        related="company_id.agx_allow_vendor_bill_cost_source", readonly=False
    )
    agx_allow_landed_cost_source = fields.Boolean(
        related="company_id.agx_allow_landed_cost_source", readonly=False
    )
    agx_margin_precision = fields.Integer(
        related="company_id.agx_margin_precision", readonly=False
    )

    # Lots
    # MRP integration
    agx_use_mrp_production = fields.Boolean(
        string="Use Manufacturing Orders (MRP) instead of Production Batches",
        default=False,
        help=(
            "When ON:  Production Batches menu is hidden.  "
            "Manufacturing menu (MRP) is shown instead.  "
            "When OFF: Production Batches menu is visible.  "
            "MRP menu visibility is controlled by Odoo's own MRP module. "
            "IMPORTANT: Existing batch records are never deleted — "
            "they remain in the database.  Only the menu visibility changes."
        ),
    )

    agx_use_mrp_production = fields.Boolean(
        related="company_id.agx_use_mrp_production", readonly=False,
        string="Use Manufacturing Orders (MRP) instead of Production Batches",
    )

    def set_values(self):
        """Toggle Production Batches menu visibility based on MRP setting."""
        res = super().set_values()
        use_mrp = self.agx_use_mrp_production
        # Find the Production Batches menu and toggle it
        batch_menu = self.env.ref(
            "agri_export_management.menu_agx_batch", raise_if_not_found=False
        )
        if batch_menu:
            batch_menu.sudo().write({"active": not use_mrp})
        # Also toggle the batch analytics menu
        batch_analytics_menu = self.env.ref(
            "agri_export_management.menu_agx_batch_analysis",
            raise_if_not_found=False,
        )
        if batch_analytics_menu:
            batch_analytics_menu.sudo().write({"active": not use_mrp})
        return res
    agx_auto_generate_lot_numbers = fields.Boolean(
        related="company_id.agx_auto_generate_lot_numbers", readonly=False
    )

    # Locations
    agx_raw_material_location_id = fields.Many2one(
        related="company_id.agx_raw_material_location_id", readonly=False
    )
    agx_production_location_id = fields.Many2one(
        related="company_id.agx_production_location_id", readonly=False
    )
    agx_finished_goods_location_id = fields.Many2one(
        related="company_id.agx_finished_goods_location_id", readonly=False
    )
    agx_cold_storage_location_id = fields.Many2one(
        related="company_id.agx_cold_storage_location_id", readonly=False
    )

    # Picking types
    agx_internal_picking_type_id = fields.Many2one(
        related="company_id.agx_internal_picking_type_id", readonly=False
    )
    agx_outgoing_picking_type_id = fields.Many2one(
        related="company_id.agx_outgoing_picking_type_id", readonly=False
    )


class SaleOrder(models.Model):
    """Auto-links intercompany Sale Orders to AGX Evaluations.

    When Odoo creates a Sale Order via intercompany rules
    (auto_purchase_order_id is set), this override searches for an
    active AGX Evaluation for the same partner / season and links it.

    This removes the need for manual linking in most cases.
    """

    _inherit = "sale.order"

    def _agx_auto_link_evaluation(self):
        """Find and link matching AGX evaluation after intercompany SO creation."""
        for so in self:
            # Only process intercompany SOs (have auto_purchase_order_id)
            if not getattr(so, 'auto_purchase_order_id', False):
                continue
            # Already linked
            if self.env['agx.evaluation'].search(
                [('intercompany_so_id', '=', so.id)], limit=1
            ):
                continue
            # Find evaluation: same company, approved, same partner, active season
            eval_rec = self.env['agx.evaluation'].search(
                [
                    ('company_id', '=', so.company_id.id),
                    ('state', '=', 'approved'),
                    ('partner_id', '=', so.partner_id.id),
                    ('season_id.state', '=', 'active'),
                    ('intercompany_so_id', '=', False),
                ],
                limit=1,
                order='evaluation_date desc',
            )
            if eval_rec:
                eval_rec.intercompany_so_id = so.id
                eval_rec.message_post(
                    body=(
                        "Intercompany Sale Order %s auto-linked from company %s."
                        % (so.name, so.company_id.name)
                    )
                )

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        records._agx_auto_link_evaluation()
        return records
