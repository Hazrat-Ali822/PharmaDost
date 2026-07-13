# Intentionally empty.
#
# Stock deduction and price defaults used to live here as pre_save/post_delete
# signal handlers, which caused double stock deduction and batch drift. All sale
# logic now lives in sales.services (create_sale / return_sale) as the single
# source of truth. This module is kept only to avoid stale imports.
