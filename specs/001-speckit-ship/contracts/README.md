# Interface Contracts: SpecKit Ship

Four interfaces cross a boundary somebody else depends on. Each is versioned independently.

| Contract | File | Consumers | Breaking change requires |
|---|---|---|---|
| Command surface | [commands.md](./commands.md) | Developers, AI integrations, `.specify/extensions.yml` hooks | New command name, or an `extension.yml` major bump |
| Ship state file | [ship-state.schema.json](./ship-state.schema.json) | The Ship view, `/speckit-ship-status`, any future community consumer | `schema_version` bump + reader migration |
| Ship configuration file | [ship-config.schema.json](./ship-config.schema.json) | The Ship view (writes), the pipeline (reads) | `schema_version` bump + validation update |
| Hosting client | [hosting-client.md](./hosting-client.md) | The engine; a second implementation for tests, and for any future hosting service | Interface change across both implementations |

**The state file is the load-bearing one.** FR-042 requires the pipeline and the view to be
separable — pipeline fully usable with no view installed, view degrading to an honest empty
state with no pipeline history. That separability is real only because neither side calls the
other; they meet at this file. It is designed once, with its consumer in view, and the view
never writes it.
