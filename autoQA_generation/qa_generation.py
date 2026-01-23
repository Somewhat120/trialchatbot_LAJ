import os
import json
import glob
import random
from collections import Counter
from pathlib import Path
from google import genai
from google.genai import types

# --- CONFIGURATION ---
# Get the script directory to construct relative paths
SCRIPT_DIR = Path(__file__).parent
INPUT_DIR = SCRIPT_DIR / "icf_evidence"
OUTPUT_DIR = SCRIPT_DIR / "generated_faqs"
MODEL_ID = "gemini-3-flash-preview"
QUESTIONS_PER_TRIAL = 8
TEST_MODE = False  # Set to True to process only the first trial

# Derived from your "Question Focus" image statistics
# Fixed: Added missing elements and verified all names match ICF_element.csv
ELEMENT_WEIGHTS = {
    "Procedures": 18,
    "Risks or Discomforts": 12,
    "Eligibility": 8,
    "Purpose of the Research": 5,
    "Benefits": 5,
    "Alternatives": 5,
    "Compensation for Participation and Time": 5,
    "Compensation & Treatment for Injury": 4,
    "Voluntariness & Withdrawal": 10,
    "Confidentiality": 2,
    "Unknown Risks": 1,
    "Use of Data/Samples in Future Research": 2,
    "Contact Information": 1,
    "Significant New Findings": 1
}

def get_client():
    """Initializes the Gemini client."""
    # Try to load from file first (like icf_extractor.py)
    api_key_path = SCRIPT_DIR / "gemini_key.txt"
    if api_key_path.exists():
        with open(api_key_path, 'r') as f:
            api_key = f.read().strip()
    else:
        # Fallback to environment variable
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            raise ValueError("Please set GEMINI_API_KEY environment variable or provide gemini_key.txt file.")
    
    os.environ["GEMINI_API_KEY"] = api_key
    return genai.Client(api_key=api_key)

def sample_elements(available_elements, k=5):
    """
    Selects 'k' elements to generate questions for, based on the 
    dominance ranking provided in the image.
    """
    # 1. Filter weights to only include elements present in this specific trial
    valid_weights = {
        k: ELEMENT_WEIGHTS.get(k, 1.0) # Default weight 1.0 if not in list
        for k in available_elements
    }
    
    population = list(valid_weights.keys())
    weights = list(valid_weights.values())
    
    if not population:
        return []

    # 2. Weighted sampling with replacement (allows multiple qs on important topics)
    selected = random.choices(population, weights=weights, k=k)
    return selected

def generate_faq_for_trial(client, file_path):
    # 1. Load Data
    with open(file_path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    
    trial_id = data.get('trial_id', 'UNKNOWN')
    evidence_map = data.get('evidence_by_element', {})
    
    # Extract cancer type from file path or trial data if available
    # For now, we'll need to infer or add it to the extraction output
    # Assuming we can get it from the file path structure or add it later
    cancer_type = "unknown"  # TODO: Extract from trial data or file path
    
    # 2. Select Elements based on ranking
    available_keys = list(evidence_map.keys())
    if not available_keys:
        print(f"Skipping {trial_id}: No evidence found.")
        return

    target_elements = sample_elements(available_keys, k=QUESTIONS_PER_TRIAL)
    
    # Count how many questions should be generated for each element
    element_counts = Counter(target_elements)
    
    # Calculate MIXED questions count
    MIXED_Q_COUNT = max(1, round(0.15 * QUESTIONS_PER_TRIAL))
    
    # Number evidence snippets per element for evidence_ids
    numbered_evidence = {}
    for element, snippets in evidence_map.items():
        numbered_evidence[element] = [
            {"id": idx + 1, "snippet": snippet['snippet']}
            for idx, snippet in enumerate(snippets)
        ]
    
    # 3. Construct Prompt with correct input format
    prompt_text = f"""CORE CONSTRAINTS:
1. Generate exactly {QUESTIONS_PER_TRIAL} question–answer pairs total for this trial.

2. The following elements should be used with the specified question counts (this reflects weighted importance):
{chr(10).join([f"   - {element}: {count} question(s)" for element, count in sorted(element_counts.items(), key=lambda x: x[1], reverse=True)])}

2b. MIXED QUESTIONS REQUIREMENT (general + trial-specific):
- Generate exactly {MIXED_Q_COUNT} of the {QUESTIONS_PER_TRIAL} questions as MIXED questions.
- A MIXED question must include:
  (a) one general concept component (e.g., placebo, randomization, trial phases, informed consent, withdrawal rights, clinical trial basics), AND
  (b) one trial-specific component that is answerable using the extracted evidence.
- In answer_good:
  - The trial-specific part MUST be supported by the extracted evidence.
  - The general concept part may use standard general knowledge, but must NOT add trial-specific facts not in evidence.
  - If the ICF does not specify the needed trial-specific detail, explicitly say: "The ICF does not specify..."
- MIXED questions can be assigned to any element, but must still follow the element count distribution above.

3. Each question must reflect something a potential participant might realistically ask to better understand the trial.

4. Each question MUST be assigned to exactly ONE ICF element.
5. For elements with multiple questions, generate DIFFERENT questions covering different aspects of that element.

6. answer_good:
   - Must be fully supported by the provided evidence snippets.
   - Do NOT introduce trial-specific facts not present in the evidence.
   - If the evidence does not specify something, say so explicitly (e.g., "The ICF does not specify…").

7. answer_drifted (optional but preferred):
   - Write a plausible but lower-quality answer.
   - Drift may affect clinical responsibility, factual accuracy, completeness, safety/ethics, relevancy, verbosity, or clarity.
   - Drift should be realistic and subtle.
   - Drifted answers do NOT need to be grounded.

8. evidence_ids:
   - For each question, include a list of snippet IDs (numbers) that support the answer_good.
   - These IDs correspond to the numbered snippets in the EXTRACTED_EVIDENCE section below.
   - Only include IDs for snippets that actually support claims made in answer_good.
   - Format: [1, 3, 5] (list of integers)

9. Do NOT include error labels, explanations, or comments.
10. Output JSON ONLY as a JSON array.

TRIAL_ID: {trial_id}
CANCER_TYPE: {cancer_type}
TARGET_QA_COUNT: {QUESTIONS_PER_TRIAL}
MIXED_QUESTIONS_COUNT: {MIXED_Q_COUNT}

EXTRACTED_EVIDENCE (numbered by element):
<<<
{json.dumps(numbered_evidence, indent=2)}
>>>

REQUIRED OUTPUT FORMAT (JSON array):
[
  {{
    "element": "Purpose of the Research",
    "question": "Why is this study being done?",
    "answer_good": "...",
    "answer_drifted": "...",
    "evidence_ids": [1, 2]
  }}
]
"""

    # 4. Call Gemini
    try:
        response = client.models.generate_content(
            model=MODEL_ID,
            contents=[
                types.Content(
                    role="user",
                    parts=[types.Part.from_text(text=prompt_text)],
                ),
            ],
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                system_instruction=[
                    types.Part.from_text(text="You are a helpful assistant creating FAQ datasets for clinical trials.")
                ]
            )
        )
        
        # 5. Parse response and add evidence_snippets from evidence_ids
        result = json.loads(response.text)
        
        # Add evidence_snippets for each item based on evidence_ids
        for item in result:
            element = item.get('element')
            evidence_ids = item.get('evidence_ids', [])
            
            if element in numbered_evidence and evidence_ids:
                # Map evidence_ids to actual snippets
                element_snippets = numbered_evidence[element]
                item['evidence_snippets'] = [
                    snippet['snippet'] 
                    for snippet in element_snippets 
                    if snippet['id'] in evidence_ids
                ]
                # Keep evidence_ids in output for auditability
            elif element in evidence_map:
                # Fallback: if no evidence_ids provided, use all snippets for element
                item['evidence_snippets'] = [
                    snippet['snippet'] for snippet in evidence_map[element]
                ]
                print(f"  ⚠️  Warning: No evidence_ids provided for {trial_id}, element '{element}'. Using all snippets.")
            else:
                item['evidence_snippets'] = []
                print(f"  ⚠️  Warning: Element '{element}' not found in evidence map for {trial_id}")
        
        # 6. Save Result with evidence_snippets included
        output_filename = Path(file_path).name.replace("_extracted.json", "_faq.json")
        output_path = OUTPUT_DIR / output_filename
        
        with open(output_path, "w", encoding='utf-8') as f:
            json.dump(result, f, indent=2, ensure_ascii=False)
            
        print(f"✅ Generated FAQs for {trial_id} -> {output_path}")

    except json.JSONDecodeError as e:
        print(f"❌ Error parsing JSON response for {trial_id}: {e}")
        if 'response' in locals():
            print(f"   Response text: {response.text[:500]}...")
    except Exception as e:
        print(f"❌ Error processing {trial_id}: {e}")

def main():
    # Ensure output directory exists
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    
    client = get_client()
    
    # Find all evidence files
    files = sorted(glob.glob(str(INPUT_DIR / "*_extracted.json")))
    
    if not files:
        print(f"❌ No evidence files found in {INPUT_DIR}")
        return
    
    # Test mode: process only the first trial
    if TEST_MODE:
        files = files[:1]
        print(f"🧪 TEST MODE: Processing 1 trial only")
    
    print(f"Found {len(files)} evidence file(s) to process.")
    print("=" * 60)
    
    for idx, file_path in enumerate(files, 1):
        print(f"[{idx}/{len(files)}] Processing: {Path(file_path).name}")
        generate_faq_for_trial(client, file_path)
    
    print("\n" + "=" * 60)
    print(f"✅ Completed processing {len(files)} trial(s)")

if __name__ == "__main__":
    main()