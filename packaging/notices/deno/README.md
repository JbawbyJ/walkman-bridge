# Deno 2.9.5 notice provenance

This directory accompanies the local unsigned Night Ops review builds. It is a
verified subset of upstream licenses, not a complete third-party notice export.
Before publishing, reconcile the complete Rust/native dependency inventory and
its license/notice obligations against the exact Windows release binary. The
files here do not establish that this publishing prerequisite is complete.

The bundled runtime reports `deno 2.9.5 (stable, release,
x86_64-pc-windows-msvc)`, V8 `15.0.245.2-rusty`, and TypeScript `6.0.3`.
The official [Deno v2.9.5 Cargo.lock](https://github.com/denoland/deno/blob/v2.9.5/Cargo.lock)
pins the `v8` Rust crate to `150.4.0`. That crate's official
[submodule tree](https://api.github.com/repos/denoland/rusty_v8/git/trees/v150.4.0)
pins the [Deno V8 fork](https://github.com/denoland/rusty_v8/blob/v150.4.0/.gitmodules)
to `ac1e23989121713ca642f6650b34deff7b686896`.

| Local file | Provenance |
| --- | --- |
| LICENSE.md | Official Deno v2.9.5 MIT license, retained unchanged |
| LICENSE-NODE-TYPES.txt | Node declaration license vendored in Deno v2.9.5 |
| LICENSE-UNDICI.txt | Undici declaration license vendored in Deno v2.9.5 |
| LICENSE-WEBGPU.md | WebGPU extension license in Deno v2.9.5 |
| LICENSE-TYPESCRIPT.txt | Official Microsoft TypeScript v6.0.3 license |
| LICENSE-RUSTY-V8.txt | Official rusty_v8 v150.4.0 license |
| LICENSE-V8.txt | V8 license from the exact pinned submodule commit |

`PROVENANCE.json` records each newly downloaded file's official HTTPS URL,
byte count, and SHA-256. Upstream contents were saved without rewriting them.
V8's license itself identifies externally maintained components; this file is
not a substitute for reconciling their separate notices with the shipped build.

The shipped `deno --help` and the official
[v2.9.5 CLI flags source](https://github.com/denoland/deno/blob/v2.9.5/cli/args/flags.rs)
provide no `--license` flag or license-export subcommand. The
[official release asset inventory](https://api.github.com/repos/denoland/deno/releases/tags/v2.9.5)
also contains no aggregate third-party notice asset. The zero-byte
`THIRD-PARTY-NOTICES.txt` left by the failed flag invocation was removed rather
than represented as a complete license inventory. A full dependency/source-based
inventory must be prepared or obtained from upstream before public release.
