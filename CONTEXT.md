# LLM Wiki Knowledge Graph

This context describes how source material becomes governed, traceable knowledge for the Harness learning Agent demo.

## Language

**Authoritative Source**:
The original Markdown material whose located passages are the final basis for accepting domain knowledge.
_Avoid_: Graph database, LLM output

**Candidate Assertion**:
A proposed node, relationship, or claim extracted from an Authoritative Source but not yet accepted as knowledge.
_Avoid_: Fact, graph fact

**Accepted Assertion**:
A reviewed structured claim that is backed by a located passage in an Authoritative Source and may appear in the authoritative knowledge graph.
_Avoid_: LLM conclusion, unreviewed relation

**Evidence Reference**:
A stable locator from an assertion to a specific passage in an Authoritative Source, including its file and heading path.
_Avoid_: Citation without a passage locator

**Assertion Ledger**:
The governed record of Candidate Assertions, Accepted Assertions, rejected assertions, their evidence, and review history.
_Avoid_: Knowledge graph, Neo4j database

**Query Graph**:
A query-oriented projection containing only Accepted Assertions, optimized for traversal and visualization.
_Avoid_: Source of truth, assertion ledger

**Entity Dictionary**:
The reviewed registry of graph entities, their stable IDs, canonical names, types, and aliases. Unrecognized names become candidates rather than new accepted entities.
_Avoid_: Automatically extracted entity list, display-name index

**Source Fragment**:
A versioned, addressable passage parsed from an Authoritative Source and used as evidence for assertions.
_Avoid_: Whole document, unlocated excerpt

**Review Event**:
An immutable record that accepts, rejects, or corrects a Candidate Assertion while preserving what the extractor originally proposed.
_Avoid_: Direct JSON overwrite, silent correction

**AI Learning Workspace**:
A local-first environment in which a person builds governed knowledge from trusted material, explores it, and learns through grounded AI assistance.
_Avoid_: Chatbot, single learning agent

**Knowledge Project**:
A user-owned collection of source material, governed assertions, a query graph, and later learning records for one learning scope.
_Avoid_: Database, chat session

**Owner-Learner**:
The primary first-version user who imports and reviews their own trusted material and then learns from the resulting Knowledge Project.
_Avoid_: Administrator, separate teacher and student roles
