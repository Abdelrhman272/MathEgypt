# Agricultural Export Management

**Author:** NextGen Systems
**Version:** 19.0.5.0.0
**License:** LGPL-3
**Odoo:** 19.0

## Overview

A complete end-to-end Odoo 19 module for agricultural export companies.
Covers the full journey from farm evaluation and procurement through
production batching, cold storage, lot-level inventory control, export
shipment management, logistics costing, and real-time profitability
dashboards — with full traceability at every step.

## Workflow

```
Farm Evaluation
     ↓ Approve
Purchase Order  ←── vendor / farm
     ↓ Confirm + Receive
Goods Receipt   (raw material → Raw Material Location)
     ↓
Production Batch
  Consume: Raw Material → Production Location
  Output:  Production Location → Finished Goods
     ↓ (optional)
Move to Cold Storage
  Transfer: Finished Goods → Cold Storage Location
     ↓
Export Shipment
  Reserve Lots (FIFO, grade/size filtered)
  Create Sales Order (revenue link)
  Mark Shipped → Delivery Order (Cold Storage → Customer)
```

## Key Features

| Feature | Description |
|---|---|
| Farm Evaluations | Pre-purchase yield assessment per grade/size with achievement % |
| Production Batches | Raw→finished stock moves with absorption costing |
| Cold Storage | Optional refrigeration step with dedicated picking |
| Export Shipments | Container management, lot reservation, delivery automation |
| Logistics Costing | Vendor bill / landed cost import, multi-basis allocation |
| Season Analytics | Auto-created analytic account per season for P&L |
| Product Variants | Initialize wizard links Grade+Size to stock variants |
| Traceability | Lot → batch → farm → shipment in one wizard |
| Dashboard | KPIs per period or season with Kanban/Graph/Pivot |

## Dependencies

```
base, mail, purchase, stock, mrp, sale_management,
account, analytic, base_setup, stock_landed_costs
```

## Installation

1. Copy `agri_export_management` into your Odoo addons path.
2. Restart the server.
3. Go to **Apps**, search *Agricultural Export*, click **Install**.

## Initial Setup (required)

Navigate to **Agricultural Export → Configuration → Settings**:

| Setting | Description |
|---|---|
| Raw Material Location | Where purchased produce lands after receipt |
| Production Location | **Must have `usage = Production`** |
| Finished Goods Location | Where batch outputs land |
| Cold Storage Location | Optional refrigerated storage |
| Internal Transfer Type | Picking type for batch moves |
| Outgoing Delivery Type | Picking type for shipment deliveries |

Then run **Configuration → Initialize Product Attributes**:
- Creates Grade and Size product attributes
- Links `agx.grade` / `agx.size` records to attribute values
- Optionally adds both attributes to finished-goods templates

## User Groups

| Group | Permissions |
|---|---|
| User | Read / Write / Create |
| Manager | User + Delete |
| Cost Analyst | User + cost reports |
| Admin | All permissions + configuration |

## Technical Notes

- `agx_flow_type` on `stock.picking` is set **at creation time**, not computed
  from moves — this prevents the field being False before moves exist.
- `agx_batch_id` / `agx_shipment_id` on `stock.picking` are plain stored fields
  so `One2many` inverse FKs work correctly.
- Reserve uses `SELECT FOR UPDATE` to prevent race conditions.
- `_unreserve_existing_delivery()` calls `do_unreserve()` before rebuilding
  the delivery to avoid ghost `reserved_quantity` in `stock.quant`.
- Cost allocation pre-aggregates denominators per shipment to avoid O(n²) queries.

## Changelog

### v19.0.5.0.0
- Added `agx.season` with auto-created analytic accounts
- Added cold storage location and batch → cold storage flow
- Added Product Variants bridge (Grade/Size ↔ product.attribute.value)
- Added Initialize Product Attributes wizard
- Added freight fields (vessel, voyage, ETD, ETA, B/L) on shipments
- Fixed Reserve double-reservation bug (do_unreserve before rebuild)
- Added Kanban views for evaluations, batches, and shipments
- Added Graph/Pivot analytics for batches and shipments
- Added Search views with filters and group-by for all models
- Professional QWeb reports with color-coded profitability
- Full docstring coverage on all models and methods

### v19.0.4.0.0
- Initial release
