# Consistency report

Source: `examples/sample_essay.txt` | mode: offline (fixtures)

## Summary

accepted: 5 | contradicts: 1 | entailed: 2 | not_entailed: 1 | quarantined: 1

## Statements

| id | type | gate | verdict | conf | statement | FOL |
|---|---|---|---|---|---|---|
| s1 | axiom | accepted | - | 0.70 | Every philosopher questions their own beliefs. | `forall x. (Philosopher(x) -> QuestionsOwnBeliefs(x))` |
| s2 | axiom | accepted | - | 0.70 | Every person who questions their own beliefs is a seeker of truth. | `forall x. (QuestionsOwnBeliefs(x) -> SeekerOfTruth(x))` |
| s3 | axiom | accepted | - | 0.95 | Socrates is a philosopher. | `Philosopher(socrates)` |
| s4 | axiom | accepted | - | 0.95 | All philosophers are human. | `forall x. (Philosopher(x) -> Human(x))` |
| s5 | axiom | accepted | - | 0.95 | Every human is mortal. | `forall x. (Human(x) -> Mortal(x))` |
| s6 | derived_claim | accepted | entailed | 0.95 | Socrates is a seeker of truth. | `SeekerOfTruth(socrates)` |
| s7 | derived_claim | accepted | entailed | 0.95 | Socrates is mortal. | `Mortal(socrates)` |
| s8 | derived_claim | accepted | contradicts | 0.95 | Socrates is not mortal. | `not Mortal(socrates)` |
| s9 | non_propositional | quarantined | - | 0.00 | The ideas of Socrates will outlive every empire that tries to bury them. | - |
| s10 | derived_claim | accepted | not_entailed | 0.95 | Some seekers of truth are never satisfied. | `exists x. (SeekerOfTruth(x) and NeverSatisfied(x))` |

## Contradictions found

### s8: “And yet I maintain that Socrates is immortal.” (chars 365-410)

- Formalized as: `not Mortal(socrates)` (it is not the case that socrates is mortal)
- Minimal conflicting set:
  - s3: “Socrates was a philosopher” (chars 169-195) -> `Philosopher(socrates)`
  - s4: “all philosophers are human” (chars 201-227) -> `forall x. (Philosopher(x) -> Human(x))`
  - s5: “Every human is mortal.” (chars 229-251) -> `forall x. (Human(x) -> Mortal(x))`
- Reading: these statements cannot all be true at once; the author has violated their own stated premises.

## Entailed claims (formally proven from the author's axioms)

- s6: “Socrates is a seeker of truth.” follows from: s1, s2, s3
- s7: “Socrates is mortal.” follows from: s3, s4, s5

## Unverifiable claims (consistent with the axioms but not provable from them)

- s10: “Some seekers of truth are never satisfied.”

## Excluded from the axiom set (nothing is dropped silently)

- s9 [quarantined]: “His ideas will outlive
every empire that tries to bury them.” :: not truth-apt (figurative, expressive, or non-assertoric)

## Theory tree

Diagram files: `graph.svg` (open in a browser or PyCharm), `graph.dot` (Graphviz), `graph.png` (if Graphviz is installed).

```text
theory cluster 0  [axioms consistent: YES]
|-- axioms (5)
|   |-- [s1] AX Every philosopher questions their own beliefs.
|   |-- [s2] AX Every person who questions their own beliefs is a see...
|   |-- [s3] AX Socrates is a philosopher.
|   |-- [s4] AX All philosophers are human.
|   `-- [s5] AX Every human is mortal.
`-- claims (4)
    |-- [s6] OK Socrates is a seeker of truth.
    |   |-- proved from [s1] AX Every philosopher questions their own beliefs.
    |   |-- proved from [s2] AX Every person who questions their own beliefs is a see...
    |   `-- proved from [s3] AX Socrates is a philosopher.
    |-- [s7] OK Socrates is mortal.
    |   |-- proved from [s3] AX Socrates is a philosopher.
    |   |-- proved from [s4] AX All philosophers are human.
    |   `-- proved from [s5] AX Every human is mortal.
    |-- [s8] XX Socrates is not mortal.
    |   |-- CONFLICTS WITH [s3] AX Socrates is a philosopher.
    |   |-- CONFLICTS WITH [s4] AX All philosophers are human.
    |   `-- CONFLICTS WITH [s5] AX Every human is mortal.
    `-- [s10] ?? Some seekers of truth are never satisfied.

excluded from theory (1)
`-- [s9] quarantined: The ideas of Socrates will outlive every empire that ... :: not truth-apt (figurative, expressive, or non-assertoric)

legend: AX axiom | OK entailed | ?? not entailed (unprovable) | XX contradicts | ~? unknown | -- excluded
```

## Cluster diagnostics

- cluster 0: 9 statements, axioms consistent: True

## Shared vocabulary

Predicates: Human, Mortal, NeverSatisfied, Philosopher, QuestionsOwnBeliefs, SeekerOfTruth

Constants: socrates
