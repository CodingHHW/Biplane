# Biplane: Multiview Target Localization with a Biplanar Fiducial Structure

[Paper: coming soon] | [Demo videos](#demo-videos) | [中文](README.zh-CN.md)

This repository provides the research code and supplementary materials for the manuscript:

> Multiview Target Localization and Navigation Using a Biplanar Fiducial Structure: A Decoupled Validation Study

The manuscript is currently under preparation/review. Paper links and final citation information will be added after publication.

<p align="center">
  <img src="docs/assets/fig_framework_overview.png" alt="Biplane framework overview" width="720">
</p>

Biplane is a 3D Slicer scripted module for validating marker-supported multiview target localization and navigation display. The module uses a known biplanar fiducial structure to estimate projection geometry, reconstruct a 3D target from two 2D observations, and forward-project the reconstructed target into an independent third view for validation.

## Repository Status

- Prepared as a research-code repository for manuscript review.
- Tested with 3D Slicer 5.8.1.
- The implementation is intended for validation and reproducibility studies rather than direct clinical deployment.

## Installation

### Prerequisites

- 3D Slicer 5.8.1.
- Windows operating system tested. macOS and Linux have not been tested, but the module is expected to work on platforms where 3D Slicer and the required Python dependencies run normally.
- No special hardware is required beyond a machine capable of running 3D Slicer.

Newer Slicer versions may require small adjustments because Slicer APIs and UI behavior can change across releases.

The module uses Slicer's Python runtime. `SimpleITK` and `opencv-python` are imported through the module's dependency helper and will be installed automatically into the Slicer environment when missing. If automatic installation fails because of network or proxy restrictions, install them manually in Slicer's Python environment.

### Load the Module

```bash
git clone https://github.com/CodingHHW/Biplane.git
```

Then open 3D Slicer:

1. Go to `Edit -> Application Settings -> Modules`.
2. Add the repository root to `Additional module paths`.
3. Restart 3D Slicer.
4. Open the `Biplane` module.

The module may currently appear under the `Examples` category.

## Workflow

### Basic Procedure

1. Load a sample volume and select it as `Input volume`.
2. Select `Perspective` or `Orthographic`.
3. Click `showVolume`, `showMarker`, and `showTestPoint`.
4. Set or verify `Markers1`, `Markers2`, and `Markers3`.
5. Capture three views with `shot1`, `shot2`, and `shot3`.
6. Click `markersSort`.
7. Select the target point in the Red view and click `redPush`.
8. Confirm the corresponding point in the Green view and click `greenPush`.
9. Inspect `TargetP3D` and `TargetP2DYellow`.
10. Optionally calculate Target Registration Error (TRE), Reprojection Error (RE), and Ray Gap.
11. Click `Export Current Results to CSV`.

### Outputs

- Intermediate screenshots and volumes: `<Slicer temporaryPath>/Biplane/`
- Experiment CSV: `experiment/experiment_results.csv`
- Transform snapshots: `experiment/transform_snapshots/<experiment_record_id>/`

## Demo Videos

The following videos document the main validation workflow and the corresponding error-analysis procedures.

<p align="center">
  <strong>Demonstration 1. Localization and navigation workflow without measurement error</strong><br>
  <a href="https://www.youtube.com/watch?v=6F34s5bbvA0">
    <img src="demo_videos/poster-localization-navigation-no-error.png" alt="Demonstration 1 preview" width="720">
  </a><br>
  <a href="https://www.youtube.com/watch?v=6F34s5bbvA0">Watch on YouTube</a>
</p>

<p align="center">
  <strong>Demonstration 2. TRE and RE calculation workflow</strong><br>
  <a href="https://www.youtube.com/watch?v=b19Zt3hKDdA">
    <img src="demo_videos/poster-tre-re-error-calculation.png" alt="Demonstration 2 preview" width="720">
  </a><br>
  <a href="https://www.youtube.com/watch?v=b19Zt3hKDdA">Watch on YouTube</a>
</p>

<p align="center">
  <strong>Demonstration 3. TRE and RE changes under noise perturbation</strong><br>
  <a href="https://www.youtube.com/watch?v=QhqlMMyc5a8">
    <img src="demo_videos/poster-noise-perturbation-tre-re-changes.png" alt="Demonstration 3 preview" width="720">
  </a><br>
  <a href="https://www.youtube.com/watch?v=QhqlMMyc5a8">Watch on YouTube</a>
</p>

## Examples and Analysis

The repository includes experiment records and analysis scripts used to support manuscript preparation:

- `experiment/experiment_results.csv`
- `Analysis/experiment_results_analysis.ipynb`
- `Analysis/projection_workflow_analysis.py`
- `Analysis/figures/experiment_results_analysis/`
- `Analysis/tables/experiment_results_analysis/`

To open the analysis notebook:

```bash
python -m venv .venv
.venv\Scripts\activate
python -m pip install -r requirements.txt
jupyter notebook Analysis/experiment_results_analysis.ipynb
```

## Data and Assets

Example test assets are stored in `Testing/`. Experiment logs and transform snapshots are stored in `experiment/`.

If any data file cannot be redistributed publicly, it should be moved to an external archive or release asset and linked here.

## Code Organization

- Main Slicer module: `Biplane.py`
- Geometry logic: `BiplaneLogics.py`
- UI resources: `Resources/`
- Public documentation assets: `docs/assets/`

## License

The source code is released under the MIT License. See `LICENSE`.

Datasets and third-party assets may require separate license statements.

## Citing Biplane

If you use this repository, please cite the associated paper and this software repository.

GitHub can currently generate a software citation from `CITATION.cff`. The final paper BibTeX will be added after publication.
