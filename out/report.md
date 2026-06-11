# Consistency report

Source: `examples/taxation_essay.txt` | mode: offline (fixtures) | effort: 1

## Summary

accepted: 2 | contradicts: 1 | quarantined: 1

## Statements

| id | type | gate | verdict | conf | statement | FOL |
|---|---|---|---|---|---|---|
| t1 | axiom | accepted | - | 0.95 | Every tax is a theft. | `forall x. (Tax(x) -> Theft(x))` |
| t2 | non_propositional | quarantined | - | 0.00 | The state takes what it has not earned. | - |
| t3 | derived_claim | accepted | contradicts | 0.95 | Some taxes are morally justified. | `exists x. (Tax(x) and MorallyJustified(x))` |
| b1 | bridge | accepted | - | 1.00 | All theft is morally unjustified (background premise, not stated in the text) | `forall x. (Theft(x) -> not MorallyJustified(x))` |

## Minimal inconsistent sets

Each set below is a minimal collection of statements that cannot all be true at once. At least one member of each set must be abandoned. The system identifies the conflict; it does not determine which member to reject.

### Inconsistent set {b1, t1, t3} (bridged)

- b1 [background premise, not stated in the text]: “All theft is morally unjustified (background premise, not stated in the text)” -> `forall x. (Theft(x) -> not MorallyJustified(x))`
- t1: “All taxation is theft.” (chars 52-74) -> `forall x. (Tax(x) -> Theft(x))`
- t3: “some taxes are morally justified” (chars 127-159) -> `exists x. (Tax(x) and MorallyJustified(x))`
- Note: this inconsistency is only detectable if you also accept b1. Without that background premise, the author's own statements remain mutually consistent.

## Excluded from the axiom set (nothing is dropped silently)

- t2 [quarantined]: “The state takes what it has not earned.” :: not truth-apt (figurative, expressive, or non-assertoric)

## Surface screener (lexical placeholder for the NLI path)

No surface-level conflicts flagged.

## Theory tree

Diagram files: `graph.svg` (open in a browser or PyCharm), `graph.dot` (Graphviz), `graph.png` (if Graphviz is installed).

```text
theory cluster 0  [axioms consistent: YES]
|-- axioms (1)
|   `-- [t1] AX Every tax is a theft.
|-- bridge premises (1)
|   `-- [b1] BR All theft is morally unjustified (background premise,...
`-- claims (1)
    `-- [t3] XX Some taxes are morally justified.
        |-- INCOMPATIBLE WITH [t1] AX Every tax is a theft.
        `-- INCOMPATIBLE WITH [b1] BR All theft is morally unjustified (background premise,...

excluded from theory (1)
`-- [t2] quarantined: The state takes what it has not earned. :: not truth-apt (figurative, expressive, or non-assertoric)

legend: AX axiom | BR bridge premise | OK entailed | ?? not entailed (unprovable) | XX member of an inconsistent set | ~? unknown | -- excluded
```

## Cluster diagnostics

- cluster 0: 3 statements, axioms consistent: True

## Timing

| stage | seconds |
|---|---|
| read_and_clean | 0.0002 |
| extraction | 0.0003 |
| translation | 0.0001 |
| gate | 0.0066 |
| bridges | 0.0002 |
| screener | 0.0000 |
| solver | 0.0179 |

## Shared vocabulary

Predicates: MorallyJustified, Tax, Theft

Constants: (none)
