# Dataset notes

## HaluMem feasibility assessment

Reviewed the public [MemTensor/HaluMem repository](https://github.com/MemTensor/HaluMem) at commit `d653749586bfc7d13f5b68e8851f52155b5b1650` on 2026-04-21, including its README, evaluation code, and Hugging Face dataset card.

### Access, format, and license

- The complete `HaluMem-Medium.jsonl` and `HaluMem-Long.jsonl` datasets are distributed through [IAAR-Shanghai/HaluMem on Hugging Face](https://huggingface.co/datasets/IAAR-Shanghai/HaluMem). The repository itself has pipeline/intermediate JSONL files; those should not be mistaken for the final evaluation dataset.
- The published final dataset is JSONL, with one JSON user object per line. The documented structure is `uuid`, `persona_info`, and `sessions`. Sessions include dialogue turns, `memory_points`, and questions. Memory points include `memory_content`, `memory_type`, `memory_source`, `is_update`, `original_memories`, importance, and timestamp.
- The dataset card declares **CC BY-NC-ND 4.0**. This is non-commercial and restricts sharing adapted material. Do not commit the dataset, transformed cases, or derived annotations to this repository. Confirm permissions for the intended use and any redistribution/publication with the rights holder or qualified counsel; this note is not legal advice.

### Fit for MemBud's proposed evaluation

HaluMem is relevant to memory update evaluation, but it is not a ready-made canonical transition dataset. Its paper/repository pipeline describes dialogues and reference memory points. The released evaluation flow selects update cases when `is_update == "True"` and `memories_from_system` is present, then compares generated memories against the reference `memory_content` and `original_memories`. The evaluator assigns `Correct Update`, `Hallucinated Update`, `Omitted Update`, or `Other` using an LLM prompt. These are not pre-existing operation-level human labels for each candidate transition.

This offers useful update-level reference material and supports update outcome categories when a system's generated memories are available. It does **not directly provide** MemBud's per-claim `required_claims`, independent material-claim annotations, explicit operation authorization, or labels for corruption of unrelated prior records. It therefore cannot, on its own, ground all planned coverage, preservation, faithfulness, and false-rejection metrics. Treat any adaptation into MemBud transitions as a separate annotation/protocol step, preserve the source IDs, and do not present the LLM-generated evaluation categories as independent ground truth.

### Recommendation

Use the hand-authored, single-reviewed (one reviewer, not independently adjudicated) transition set for the controlled graph-versus-text safety experiment. Consider HaluMem only as a separately scoped exploratory external-validity or downstream update evaluation, subject to license review and a documented mapping from its dialogue/reference fields to fixed candidate transitions. Do not put HaluMem-derived records in this repository. Pin a specific dataset revision and obtain the files through the official Hugging Face dataset page only if proceeding.
