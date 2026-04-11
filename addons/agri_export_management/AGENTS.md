# agri_export_management Agent Guide

This module is business-critical.

Think in terms of:

- farm/export operations
- lot traceability
- stock correctness
- shipment/container logic
- reservation flow
- reporting
- costing implications

Before any change, review impact on:

- stock moves
- pickings
- reservations
- lots
- shipment relations
- reports
- security
- accounting side effects

Do not:

- add blind sudo()
- duplicate business logic
- break standard Odoo stock flow

If changing validation/reservation/shipment logic:

- explain side effects
- provide validation steps in Odoo UI
