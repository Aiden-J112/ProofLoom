# Separate the assertion ledger from the query graph

The authoritative knowledge model will be maintained as an assertion ledger containing evidence and review history, while only accepted assertions are projected into a query graph such as Neo4j. This keeps provenance and governance intact without forcing traversal queries and visualization through reified assertion nodes; query-graph relationships retain an assertion ID so they can be traced back to the ledger and original Markdown passage.
