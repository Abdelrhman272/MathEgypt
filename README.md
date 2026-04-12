# Agricultural Export Management — `agri_export_management`

**Author:** NextGen Systems  
**Version:** 19.0.4.0.0  
**License:** LGPL-3

## Overview

A complete Odoo 19 module for managing agricultural export operations end-to-end:

- **Farm Evaluations** — assess expected yield per farm and crop before purchasing
- **Purchase Orders** — link POs directly to evaluations for traceability
- **Production Batches** — track raw material consumption and finished goods output with automatic inventory postings
- **Export Shipments** — manage containers, reserve lots, calculate logistics costs, and post deliveries
- **Profitability Dashboard** — monitor revenue, gross profit, and margins per period

## Dependencies

```
base, mail, purchase, stock, mrp, sale_management, account, base_setup, stock_landed_costs
```

## Installation

1. Copy `agri_export_management` into your Odoo `addons` path.
2. Restart the Odoo server.
3. Go to **Apps**, search for *Agricultural Export*, and click **Install**.

## Initial Setup

After installation, go to **Agricultural Export → Configuration → Settings** and configure:

| Setting | Description |
|---|---|
| Production Location | Where raw materials are consumed |
| Finished Goods Location | Where batch outputs land |
| Raw Material Location | Fallback source for inputs |
| Internal Transfer Type | Picking type used for batch stock moves |
| Outgoing Delivery Type | Picking type used for shipment deliveries |
| Container Service Product | Product used on auto-created Sales Orders |
| Auto-generate Lot Numbers | Automatically create lot numbers for batch outputs |

## User Groups

| Group | Permissions |
|---|---|
| Agricultural Export User | Read / Write / Create on all models |
| Agricultural Export Manager | Full access including delete |
| Agricultural Export Cost Analyst | User + cost analysis views |
| Agricultural Export Admin | All of the above |

## Key Workflows

### Evaluation → Purchase → Batch → Shipment

```
Farm Evaluation → Approve → Create PO → Receive Goods
                                             ↓
                                   Production Batch (done)
                                             ↓
                                   Export Shipment (reserve → ship)
```

## Notes for Developers

- See `AGENTS.md` for AI-agent coding conventions.
- All stock movements created by batches and shipments set `agx_flow_type` **at picking creation time** — do not rely on the computed fallback.
- The `_lock_reservation_scope` method uses `SELECT FOR UPDATE` to prevent race conditions on lot reservations.
