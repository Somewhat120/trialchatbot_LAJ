import os
import json
import csv
import glob
from pathlib import Path
from google import genai
from google.genai import types

# Get the script directory to construct relative paths
SCRIPT_DIR = Path(__file__).parent
BASE_DIR = SCRIPT_DIR.parent

def load_api_key(key_file_path):
    """Load API key from text file."""
    with open(key_file_path, 'r') as f:
        return f.read().strip()

def load_icf_elements(csv_path):
    """Load ICF elements and definitions from CSV and format for prompt."""
    elements_list = []
    with open(csv_path, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            element = row['element'].strip()
            definition = row['definition'].strip()
            if element and definition:  # Skip empty rows
                elements_list.append(f"{element}: {definition}")
    return "\n".join([f"{i+1}. {elem}" for i, elem in enumerate(elements_list)])

def load_trials(csv_path):
    """Load trial list from CSV."""
    trials = []
    with open(csv_path, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            trials.append({
                'trial_num': row['trial_num'].strip(),
                'cancer_type': row['cancer_type'].strip()
            })
    return trials

def find_all_icf_files(trial_num, cancer_type, base_dir):
    """Find ALL ICF text files for a given trial (handles multiple arms)."""
    icf_dir = base_dir / "ICF_text_cleaned" / cancer_type
    
    # Find all files matching the trial number pattern
    all_files = []
    
    # Try exact match first (no suffix)
    exact_file = icf_dir / f"{trial_num}.txt"
    if exact_file.exists():
        all_files.append(exact_file)
    
    # Then find all files with suffixes (_000, _001, _002, etc.)
    pattern_files = list(icf_dir.glob(f"{trial_num}_*.txt"))
    all_files.extend(pattern_files)
    
    # Remove duplicates and sort
    all_files = sorted(set(all_files), key=lambda x: x.name)
    
    return all_files

def load_icf_text(file_path):
    """Load ICF text from a single file."""
    if file_path and file_path.exists():
        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            return f.read()
    return None

def extract_icf_evidence(trial_num, full_icf_text, element_definition_list, api_key):
    """
    Sends ICF text and requirements to Gemini and returns the extracted JSON.
    """
    
    # Initialize Client
    client = genai.Client(api_key=api_key)

    # 2. DEFINE PROMPTS
    core_requirements = """CORE REQUIREMENTS (STRICT):
1. Return ONLY verbatim text copied from the ICF.
   - Do NOT paraphrase, summarize, explain, or infer.
   - Do NOT add commentary or interpretation.
2. Extract COMPLETE, COHERENT UNITS of text:
   - Prefer full sentences.
   - If needed, include adjacent sentences from the same paragraph to preserve meaning.
   - If content appears in a table or bullet list, return the full row or bullet.
3. The ICF text may be noisy due to OCR. Preserve original wording exactly.
4. Be COMPREHENSIVE. Use broad matching (synonyms).
5. If NO relevant text is found for an element, return exactly:
   "No relevant evidence found in ICF."
6. Output JSON ONLY. No extra text.
"""

    # The dynamic data part
    data_prompt = f"""
TRIAL_ID: {trial_num}

ICF TEXT:
<<<
{full_icf_text}
>>>

ICF ELEMENTS (with definitions):
{element_definition_list}

TASK:
For EACH listed element, extract all verbatim ICF text that supports that element.

REQUIRED OUTPUT FORMAT (JSON):
{{
  "trial_id": "{trial_num}",
  "evidence_by_element": {{
    "Element Name Example": [
      {{ "snippet": "..." }}
    ]
  }}
}}
"""
    # Combine them into the user content
    full_prompt_text = core_requirements + "\n" + data_prompt

    # 3. CONFIGURE MODEL
    model_id = "gemini-3-flash-preview"

    generate_content_config = types.GenerateContentConfig(
        thinking_config=types.ThinkingConfig(
            thinking_level="LOW",
        ),
        media_resolution="MEDIA_RESOLUTION_LOW",
        response_mime_type="application/json",
        system_instruction=[
            types.Part.from_text(text="""You are an evidence extraction assistant for clinical trial Informed Consent Forms (ICFs).
Your task is to extract ALL verbatim ICF text that supports each listed ICF element.
Optimize for RECALL."""),
        ],
    )

    # 4. CALL API
    try:
        response = client.models.generate_content(
            model=model_id,
            contents=[
                types.Content(
                    role="user",
                    parts=[types.Part.from_text(text=full_prompt_text)],
                ),
            ],
            config=generate_content_config,
        )
        
        # 5. PARSE & RETURN
        return response.text

    except Exception as e:
        return json.dumps({"error": str(e)})

def merge_extraction_results(results_list, trial_num):
    """
    Merge multiple extraction results from different arm files into a single JSON.
    """
    merged_evidence = {}
    
    for result_json in results_list:
        try:
            result = json.loads(result_json)
            
            # Handle error responses
            if "error" in result:
                continue
            
            # Merge evidence_by_element
            if "evidence_by_element" in result:
                for element, snippets in result["evidence_by_element"].items():
                    if element not in merged_evidence:
                        merged_evidence[element] = []
                    
                    # Add all snippets from this arm
                    if isinstance(snippets, list):
                        merged_evidence[element].extend(snippets)
                    elif isinstance(snippets, dict) and "snippet" in snippets:
                        merged_evidence[element].append(snippets)
        except json.JSONDecodeError:
            continue
    
    # Create final merged result
    merged_result = {
        "trial_id": trial_num,
        "evidence_by_element": merged_evidence
    }
    
    return json.dumps(merged_result, indent=2)

# --- MAIN EXECUTION ---
if __name__ == "__main__":
    # Load API key
    api_key_path = SCRIPT_DIR / "gemini_key.txt"
    api_key = load_api_key(api_key_path)
    os.environ["GEMINI_API_KEY"] = api_key
    
    # Load ICF elements
    elements_csv_path = SCRIPT_DIR / "ICF_element.csv"
    element_definition_list = load_icf_elements(elements_csv_path)
    
    # Load trials
    trials_csv_path = SCRIPT_DIR / "remaining_trials_for_autoqa.csv"
    trials = load_trials(trials_csv_path)
    
    # Track results
    successful_trials = []
    failed_trials = []
    
    print(f"Processing {len(trials)} trials...")
    print("=" * 60)
    
    # Process ALL trials
    for idx, trial in enumerate(trials, 1):
        trial_num = trial['trial_num']
        cancer_type = trial['cancer_type']
        
        print(f"[{idx}/{len(trials)}] {trial_num} ({cancer_type})", end=" ... ")
        
        try:
            # Find ALL ICF text files (handles multiple arms)
            icf_files = find_all_icf_files(trial_num, cancer_type, BASE_DIR)
            
            if not icf_files:
                error_msg = f"No ICF files found"
                print(f"FAILED: {error_msg}")
                failed_trials.append({
                    'trial_num': trial_num,
                    'cancer_type': cancer_type,
                    'error': error_msg
                })
                continue
            
            # Process each arm file separately
            all_results = []
            for icf_file in icf_files:
                icf_text = load_icf_text(icf_file)
                
                if icf_text:
                    # Extract evidence for this arm
                    json_result = extract_icf_evidence(trial_num, icf_text, element_definition_list, api_key)
                    all_results.append(json_result)
            
            if not all_results:
                error_msg = f"Could not read ICF text from files"
                print(f"FAILED: {error_msg}")
                failed_trials.append({
                    'trial_num': trial_num,
                    'cancer_type': cancer_type,
                    'error': error_msg
                })
                continue
            
            # Merge all results
            merged_result = merge_extraction_results(all_results, trial_num)
            
            # Check if result contains errors
            try:
                result_dict = json.loads(merged_result)
                if "error" in result_dict or not result_dict.get("evidence_by_element"):
                    error_msg = "API returned error or empty result"
                    print(f"FAILED: {error_msg}")
                    failed_trials.append({
                        'trial_num': trial_num,
                        'cancer_type': cancer_type,
                        'error': error_msg
                    })
                    continue
            except json.JSONDecodeError:
                error_msg = "Invalid JSON in merged result"
                print(f"FAILED: {error_msg}")
                failed_trials.append({
                    'trial_num': trial_num,
                    'cancer_type': cancer_type,
                    'error': error_msg
                })
                continue
            
            # Save to file
            output_file = SCRIPT_DIR / f"{trial_num}_extracted.json"
            with open(output_file, "w", encoding='utf-8') as f:
                f.write(merged_result)
            
            print("SUCCESS")
            successful_trials.append({
                'trial_num': trial_num,
                'cancer_type': cancer_type,
                'output_file': str(output_file),
                'num_arms': len(icf_files)
            })
            
        except Exception as e:
            error_msg = f"Exception: {str(e)}"
            print(f"FAILED: {error_msg}")
            failed_trials.append({
                'trial_num': trial_num,
                'cancer_type': cancer_type,
                'error': error_msg
            })
    
    # Save summary
    print("\n" + "=" * 60)
    print(f"SUMMARY: {len(successful_trials)} successful, {len(failed_trials)} failed")
    
    # Save success/failure logs
    summary_file = SCRIPT_DIR / "extraction_summary.json"
    with open(summary_file, "w", encoding='utf-8') as f:
        json.dump({
            'total_trials': len(trials),
            'successful': len(successful_trials),
            'failed': len(failed_trials),
            'successful_trials': successful_trials,
            'failed_trials': failed_trials
        }, f, indent=2)
    
    print(f"Summary saved to: {summary_file}")
    
    # Print failed trials if any
    if failed_trials:
        print("\nFailed trials:")
        for failed in failed_trials:
            print(f"  - {failed['trial_num']}: {failed['error']}")