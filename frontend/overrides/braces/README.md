# braces 3.0.4 (local patch)

This is [micromatch/braces](https://github.com/micromatch/braces) 3.0.3 plus a nesting cap for [GHSA-vfj7-8cjw-p6xm](https://github.com/advisories/GHSA-vfj7-8cjw-p6xm). No patched release exists on npm (`<=3.0.3` is the vulnerable range).

Brace and parenthesis nesting is limited to 100. `options.maxDepth` can only lower that cap. The parser rejects deeper strings with `SyntaxError`. Compile, expand, and stringify reject deeper or cyclic ASTs with `RangeError`. Ordinary content globs such as `*.{js,jsx}` are unchanged.

The frontend `overrides` entry installs this tree in place of the published 3.0.3 package so Tailwind 3 can stay on its current release.
