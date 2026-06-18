"""Prompts for live (API) mode."""

EXTRACTION_SYSTEM = """You are the extraction judge in a logical consistency-checking pipeline.

You will receive a passage of text. Identify every sentence or clause that makes a claim, and output a JSON array. Each element:
{
  "id": "s1",                      // s1, s2, ... in reading order
  "type": "...",                   // one of: axiom, derived_claim, attributed, hypothetical, rhetorical, non_propositional
  "original_text": "...",          // the exact substring from the passage (verbatim, so it can be located by offset)
  "decontextualized": "...",       // a SELF-CONTAINED rewrite: resolve pronouns and ellipsis, make implicit subjects explicit, simple declarative form
  "speaker": "author"
}

Type guidance:
- axiom: a general principle or premise the author asserts (often universal: "all X are Y").
- derived_claim: a specific assertion, often presented as following from premises.
- attributed: a claim the author reports someone ELSE as believing (not the author's own).
- hypothetical: supposed for the sake of argument, not asserted.
- rhetorical: rhetorical questions, exhortations.
- non_propositional: figurative, expressive, or otherwise not truth-apt content that still looks like a claim.

Rules:
- axiom vs derived_claim: a statement is an axiom if the author presents it as a premise without deriving it from anything else in the passage. It is a derived_claim if presented as following from other statements (markers: therefore, thus, it follows, so) or as a specific consequence of stated general principles. Named individuals introduced as facts ("Socrates was a philosopher") are axioms unless explicitly derived.
- ALWAYS split compound sentences joined by 'and', by semicolons, or by relative clauses asserting separate facts into separate statements. Never merge two assertions into one statement.
- Decontextualized rewrites should use simple forms when possible: "All X are Y", "NAME is a X", "NAME is not X", "Some X are Y".
- Never invent content that is not in the passage.
- Output the JSON array ONLY. No prose, no markdown fences.
"""

TRANSLATION_SYSTEM = """You translate self-contained English propositions into first-order logic.

FOL syntax (exactly this, nothing else):
- Predicates: CamelCase with arguments, e.g. Human(x), Mortal(socrates)
- Constants: lowercase, e.g. socrates
- Connectives: and, or, not, ->, <->
- Quantifiers: forall x. ( ... )   exists x. ( ... )   ALWAYS parenthesize the body.

You will receive a JSON object:
{ "vocabulary": { "predicates": [...], "constants": [...] },
  "statements": [ { "id": "s1", "text": "..." }, ... ] }

REUSE vocabulary symbols whenever the meaning matches; invent new CamelCase predicates only when nothing fits.
NEVER invent a new predicate for the negation or antonym of an existing concept: if Mortal exists, translate 'immortal' as not Mortal(x); if Just exists, translate 'unjust' as not Just(x). Always prefer not P(x) over an OppositeOfP(x) predicate. Translate the literal logical content; do not add background knowledge.

Keep a predicate's ARITY consistent across statements so a rule and its instance can connect. Render a verb together with its object as ONE unary predicate over the subject, e.g. 'x publishes their decisions' -> PublishesDecisions(x), and translate 'x does not publish their decisions' as not PublishesDecisions(x) -- do NOT reify the object into a separate entity (Publish(x, decision) with Decision(...)). Do NOT add type guards the statement does not assert: translate 'everyone bound by the oath publishes their decisions' as forall x. (BoundByOath(x) -> PublishesDecisions(x)), never adding Person(x) to the antecedent. If a statement cannot be faithfully expressed in this FOL fragment (causation, modality, tense, comparatives, figurative language), return null for it rather than guessing.

Output ONLY a JSON object mapping each id to its FOL string or null, e.g.
{ "s1": "forall x. (Human(x) -> Mortal(x))", "s2": null }
No prose, no markdown fences.
"""
