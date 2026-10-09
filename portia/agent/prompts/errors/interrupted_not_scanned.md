<!--
placeholders: {facts} — the compact JSON `handlers.interrupted_facts` returned on a warehouse: the input
sizes the catalog already held and nothing scanned.
-->
The data is in a warehouse, so portia scanned nothing after the interrupt: measuring the joins there
would be the scan the user just stopped. These are the sizes the catalog already held, and `rows: null`
means it holds none for that table:

{facts}
