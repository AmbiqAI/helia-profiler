# Proposed public CLI choice compatibility

[#459](https://github.com/AmbiqAI/helia-profiler/issues/459) concerns the private
`typer._types.TyperChoice` dependency and the related upper version cap. The
current ranges overlap with the available platform dependency range; this
proposal does not claim a present resolver conflict or treat a pin change alone
as a fix.

## Proposed boundary

The CLI owner must select a supported Typer range and public choice API before
implementation. Preserve dynamic choices derived from registered boards, engines
and transports, their defaults, help output, error messages and option aliases.
Configuration validation remains in its existing owner; parsing does not add a
second compatibility or benchmark qualification rule.

Use the smallest public-API adaptation at the existing choice construction sites.
Remove the private import only when the chosen public contract covers all current
consumers. Change the dependency cap only when the supported-version evidence
justifies it. If the agreed minimum cannot express the contract, record that
constraint rather than installing a second choice implementation.

## Acceptance and dependencies

A focused union must cover default and explicit choices, a registered custom
board, invalid-choice errors, help and relevant aliases across the agreed minimum
and selected newer release. The expected choices come from the registry contract;
do not merely snapshot a new parser's output. Preserve compatible valid commands
and verify the old private-import/cap constraint is removed by the delivered
public contract.

Protected CLI/NPU, notebook and release ownership must be reconciled before
changing shared sites or pins. No CLI redesign, indiscriminate dependency upgrade,
new model support or board campaign is included. This proposal requires a version
policy and a named compatibility-test boundary before implementation.
