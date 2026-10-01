# Publish fixed2tab 0.1.0 on conda-forge

Status: Phases A, B and C are complete. `fixed2tab` 0.1.0 is on conda-forge (`conda-forge/fixed2tab-feedstock`). Phase D is next. `recipe/recipe.yaml` and `README.md` are the source of truth for the recipe and the install commands.

## Context

We want a `fixed2tab` conda package so that a Galaxy tool (later, ideally in tools-iuc) can declare it as its requirement.

Research results:

- **Channel: conda-forge.** Bioconda accepts only bioinformatics packages, and fixed2tab is generic text processing. IUC accepts conda-forge and bioconda equally.
- **Recipe format: v1 `recipe.yaml`** (rattler-build). staged-recipes accepts `meta.yaml` only in exceptional cases.
- **Source: GitHub tag archive** (user decision). No PyPI release.
- **Containers:** tools-iuc CI runs `planemo test --biocontainers` and needs a container. Biocontainers are automatic only for bioconda. For a conda-forge package, add one line `fixed2tab=0.1.0` to `BioContainers/multi-package-containers` `combinations/hash.tsv`.
- **Name:** free on PyPI, conda-forge and bioconda. No staged-recipes PR uses the name.

User decisions:

- Version: `0.1.0`.
- Maintainers: `gautschr` and `jnussbaum`.
- Target: usegalaxy.ch (beta). We have a direct contact to its admin. tools-iuc is preferred but not required.
- No bundled-code workaround. The Galaxy tool waits for the conda-forge package, so we make the conda-forge review as fast as possible.
- Build step: pip (`pip install . -vv --no-deps --no-build-isolation`), not uv. In a conda build, conda resolves the dependencies, so uv gives no benefit, and the pip line is the conda-forge standard that reviewers check for.
- Local build tool: rattler-build. It is the only mature builder for v1 recipes, and conda-forge CI uses it. `conda-build` reads only `meta.yaml`.

Evidence on containers: `galaxyproject/planemo-ci-action` `planemo_ci_actions.sh` tests every tool with `--biocontainers --no_dependency_resolution` by default. Only the paths in `.tt_biocontainer_skip` use conda.

Before Phase A, the repo had a stdlib-only package, a `noarch: python` local recipe in `conda-recipe/meta.yaml`, and CI checks for the sdist, the wheel and the version sync.

## Phase A — v1 recipe in this repo (branch `claude/fixed2tab-conda-package-b325b9`)

1. Replace `conda-recipe/meta.yaml` with `recipe/recipe.yaml` (v1 format). The local recipe builds from the working tree (`source: path: ..`). Content:
   - `context:` with `version: "0.1.0"` and `python_min: "3.10"`.
   - `build:` with `number: 0`, `noarch: python`, `script: ${{ PYTHON }} -m pip install . -vv --no-deps --no-build-isolation`, and `python.entry_points: [fixed2tab = fixed2tab.cli:main]`.
   - `requirements:` with host `python ${{ python_min }}.*`, `pip`, `hatchling`, and run `python >=${{ python_min }}`.
   - `tests:` with a `python` test (`imports: [fixed2tab]`, `pip_check: true`, `python_version: ${{ python_min }}.*`) and a `script` test (`fixed2tab --version`, `fixed2tab --help`).
   - `about:` with the fields of the current `meta.yaml`, renamed to v1 keys (`homepage`, `repository`, `documentation`, `license: MIT`, `license_file: LICENSE`).
   - `extra.recipe-maintainers:` with `gautschr` and `jnussbaum`.
   - A header comment. The comment names the one difference from the conda-forge copy: `source` uses `url` and `sha256`, not `path`.
2. Set `__version__ = "0.1.0"` in `src/fixed2tab/__init__.py`. Update its docstring: the conda recipe takes the version from the GitHub tag archive, not from a PyPI sdist.
3. Update the CI step "Conda recipe version matches the package" in `.github/workflows/ci.yml`. The step reads `recipe/recipe.yaml` and matches `version: "..."` under `context`.
4. Add a CI job that builds the recipe with `prefix-dev/rattler-build-action`. The job proves that the recipe builds and that its tests pass.
5. Move the local conda build in `README.md` and `docs/running-fixed2tab.md` to a developer section:
   - Use `rattler-build build --recipe recipe` and `conda create -n fixed2tab-local -c ./output -c conda-forge fixed2tab`.
   - Keep the build-number warning, with the `recipe/recipe.yaml` path.
   - Remove the mention of PyPI. The package is published only on conda-forge.
   - Add the end-user install section (`conda install -c conda-forge fixed2tab`, or `pixi add fixed2tab`) only in Phase C step 6, when the command works.
6. Verify locally:
   - Run `pytest`, `ruff check`, `ruff format --check`, `mypy`.
   - Run `rattler-build build --recipe recipe`, install the result into a fresh environment, and run `fixed2tab --version` (expect `0.1.0`).
7. Commit. Push and open a PR only when the user tells me to. Assign the PR to `jnussbaum`.

## Phase B — release 0.1.0 (needs user approval, outward-facing)

1. After the merge, tag `v0.1.0` on `main` and create a GitHub release.
2. Download `https://github.com/dasch-swiss/rdu-fixed2tab/archive/refs/tags/v0.1.0.tar.gz` and compute its SHA256.
3. Check that the archive contains `LICENSE` and `pyproject.toml`. Then run `pip install` from the archive. hatchling reads the version from `__init__.py`, so git metadata is not needed.

## Phase C — conda-forge staged-recipes PR (needs user approval, outward-facing)

1. Fork `conda-forge/staged-recipes`. Copy `recipe/recipe.yaml` to `recipes/fixed2tab/recipe.yaml` and make one change: `source: url: …/v${{ version }}.tar.gz` plus `sha256`.
2. Build locally with `python build-locally.py` (or `rattler-build`). Then lint with `conda-smithy recipe-lint`.
3. Open the PR, using the staged-recipes PR template checklist. `gautschr` and `jnussbaum` each confirm in a comment that they agree to maintain the recipe.
4. To make the review fast, do these steps:
   - Open the PR on the day of the release, only after the recipe passes lint and builds locally.
   - Fill in every item of the template checklist, so that reviewers have nothing to ask for.
   - When CI is green, ping `@conda-forge/staged-recipes` and add a short request in the conda-forge Zulip.
   - Answer review comments on the same day.
   - A small `noarch: python` recipe with no dependencies often merges within a few days, but that is not guaranteed.
5. After the merge, check that `conda-forge/fixed2tab-feedstock` exists and that `conda install -c conda-forge fixed2tab=0.1.0` works.
6. Add the end-user install section to `README.md` and `docs/running-fixed2tab.md` (a small follow-up PR).

## Parallel to Phase C — ask the usegalaxy.ch admin

Ask the admin which dependency resolver usegalaxy.ch uses: conda, Docker, or Singularity containers. Claude drafts the message, and a maintainer sends it. The answer decides the next step:

- Conda resolver: the tool can go live as soon as conda-forge publishes. Phase D is needed only for tools-iuc.
- Containers: Phase D is needed before the tool can run on usegalaxy.ch.

## Phase D — container for Galaxy (after the feedstock publishes)

Why: tools-iuc CI and container-based servers need an image. BioContainers builds images automatically only for bioconda packages.

1. Look at the `jq` tags on quay.io to find the image name that a single-package entry produces.
2. Open a PR on `BioContainers/multi-package-containers` that adds `fixed2tab=0.1.0` to `combinations/hash.tsv`. Copy the format of the single-package conda-forge lines (`jq=1.6`).
3. After the merge, write down the published `quay.io/biocontainers/…` image tag. The Galaxy wrapper needs it for `planemo test --biocontainers`.

## Out of scope

The Galaxy wrapper itself (`macros.xml`, tool XML, `.shed.yml`, a tools-iuc PR) gets a separate plan after Phase C. `@TOOL_VERSION@` will be `0.1.0`.

## Verification

- Phase A: the local checks in step A6 pass, and CI is green, including the rattler-build job.
- Phase C: staged-recipes CI is green on linux-64. `noarch` builds only there.
- End-to-end: `conda create -n t -c conda-forge fixed2tab=0.1.0 && conda run -n t fixed2tab --version` prints `0.1.0`.
