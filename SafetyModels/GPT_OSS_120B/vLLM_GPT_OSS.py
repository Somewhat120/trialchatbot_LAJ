# https://huggingface.co/openai/gpt-oss-20b


import os
# This must come BEFORE 'import torch'
os.environ["PYTORCH_ALLOC_CONF"] = "expandable_segments:True"
# Set this environment variable specifically for the OpenMP conflict
os.environ["KMP_DUPLICATE_LIB_OK"] = "TRUE"


import warnings
from openai import OpenAI

client = OpenAI(
    base_url="http://localhost:8000/v1",
    api_key="EMPTY"
)


warnings.filterwarnings("ignore", category=UserWarning)
warnings.filterwarnings("ignore", message=".*PYTORCH_CUDA_ALLOC_CONF.*")


import ReadFile, torch, re, json
from tqdm import tqdm
from sklearn.metrics import confusion_matrix
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

MODEL_ID = "gpt-oss-safeguard-120b"


class HarmonyClassifier:
    def __init__(self, base_url="http://localhost:8000/v1", api_key="EMPTY"):
        self.client = OpenAI(base_url=base_url, api_key=api_key)
        self.model = MODEL_ID

    def _call_vllm(self, args):
        messages, max_tokens = args
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=0.0,
                max_tokens=max_tokens,
            )
            return response.choices[0].message.content
        except Exception as e:
            print(f"API Error on a single prompt: {e}")
            return "Error: API call failed"

    def generate(self, messages_list, max_tokens=3500):
        tasks = [(messages, max_tokens) for messages in messages_list]

        with ThreadPoolExecutor(max_workers=10) as executor:
            responses = list(executor.map(self._call_vllm, tasks))

        return responses


def parse_harmony_output(raw_text, format):
    if format == 'json':
        start_idx = raw_text.find('{')
        end_idx = raw_text.rfind('}')

        if start_idx != -1 and end_idx != -1:
            json_string = raw_text[start_idx: end_idx + 1]
        else:
            raise ValueError("No JSON brackets {} found in the model output.")

        try:
            extracted_dict = json.loads(json_string)
        except json.JSONDecodeError as e:
            print(f"JSON Parsing Error: {e}\nRaw String: {json_string}")
            raise ValueError("Could not parse extracted text as JSON.")

        keys = list(extracted_dict.keys())
        if len(keys) >= 2:
            reasoning = extracted_dict[keys[0]]
            clean_label = extracted_dict[keys[1]]
        else:
            reasoning = extracted_dict[keys[0]]
            clean_label = int(reasoning[-1])
    elif format == 'token':
        clean_label = raw_text.replace("*", "").strip()
        reasoning = 'None'

    return clean_label, reasoning


def pipeline(add_sys_prompt: bool, batch_size: int, model_object: HarmonyClassifier) -> list:
    pred_lbl_list = []
    reasoning_cache_list = []

    for indx in range(0, len(prompt_list), batch_size):

        prompt_batch = prompt_list[indx: indx + batch_size]
        answer_batch = answer_list[indx: indx + batch_size]
        batch_chunk = list(zip(prompt_batch, answer_batch))
        messages_batch = []
        for sample in batch_chunk:
            user_prompt = user_prompt_content.replace("{question}", sample[0]).replace("{answer}", sample[1])
            if add_sys_prompt:
                rubric = f"Reasoning: {thinking_level}\n {system_prompt_content}"
                messages = [
                    {"role": "system", "content": rubric},
                    {"role": "user", "content": user_prompt}
                ]
            else:
                messages = [
                    {"role": "system",
                     "content": f"Reasoning: {thinking_level}"},
                    {"role": "user", "content": user_prompt}
                ]
            messages_batch.append(messages)

        output = model_object.generate(messages_batch)

        for result in output:
            label, reason_ = parse_harmony_output(result, format)
            pred_lbl_list.append(label)
            if reasoning_access:
                reasoning_cache_list.append(reason_)

    return pred_lbl_list, reasoning_cache_list


def conf_matrx(y_true, y_pred) -> str:
    print(Counter(y_true))
    cm = confusion_matrix(y_true, y_pred)
    print(cm)
    ReadFile.cm_log(cm)


if __name__ == "__main__":
    data_name = "Full_Data.xlsx"
    batch_size = 10
    sample_size_per_iteration = 100
    add_sys_prompt = False
    reasoning_access = False
    gold_label_choice = int(input("""What safety category you want to test? \n
                    1:Presence of critical harms risks,\n
                    2:Presence of individualized medical advice,\n
                    3:Presence of inappropriate persuasion\n """))

    gold_label_choice_decoder = {1: 'Presence of critical harms risks', 2: 'Presence of individualized medical advice',
                                 3: 'Presence of inappropriate persuasion'}
    print(f"\nWARNING: You chose to evaluate {gold_label_choice_decoder[gold_label_choice]}\n")

    sys_prompt_dict = {1: '1_critical_harm_v1', 2: '2_individualized_advice_v1',
                       3: '3_persuasion_v1'}
    sys_prompt_decision = input("Do you wish to test with your sys_prompt y/n ? ")
    if sys_prompt_decision == "y":
        sys_prompt = True
        print(f"\n You decided to apply your chosen sys_prompt. \n CHOICE: {sys_prompt_dict[gold_label_choice]}\n")
        data = ReadFile.read_json_file(f"{sys_prompt_dict[gold_label_choice]}.json")
        system_prompt_content = data["system_role"]
        user_prompt_content = data["instruction_template"]
        print(system_prompt_content + '\n')
    else:
        sys_prompt = False
        print("\n You decided to continue with **NO** sys_prompt. \n")

    user_choice = input("Do you wish to see the CoT ? y/n: ")
    if user_choice == "y":
        reasoning_access = True
        print("\n WARNING: You chose to access to model's CoT.")
    else:
        print("\n WARNING: You chose **NOT** to access to model's CoT.")

    thinking_level_choice = input("What level of thinking do you expect ? l/m/h: ")
    if thinking_level_choice == "l":
        thinking_level = 'low'
    elif thinking_level_choice == "m":
        thinking_level = 'medium'
    elif thinking_level_choice == "h":
        thinking_level = 'high'
    else:
        thinking_level = 'medium'
    print(f"\n WARNING: You chose {thinking_level} level thinking approachGPT.")

    output_format_decision = input("Do you expect json (CoT + Label) output format? y/n: ")
    if output_format_decision == "y":
        format = 'json'
        print("\n WARNING: You chose json format output scenario!")
    else:
        format = 'token'
        print("\n WARNING: You chose token format output scenario!")


    print("\nConnecting to vLLM server...")
    model_obj = HarmonyClassifier()

    gold_lbl_decode = {0: 0, 1: 1, 2: 1, 3: 1}
    input_data = ReadFile.read_xlsx(data_name)
    # input_data = ReadFile.read_csv(data_name)
    prompt_list_total = input_data['question'].tolist()  # [80:105]
    answer_list_total = input_data['answer'].tolist()  # [80:105]
    answer_gold_lbl_total_en = input_data[gold_label_choice_decoder[gold_label_choice]].tolist()  # [80:105]
    answer_gold_lbl_total = [gold_lbl_decode[x] for x in answer_gold_lbl_total_en]

    failed_indices = []
    for index in tqdm(range(0, len(prompt_list_total), sample_size_per_iteration)):
        try:
            print(f' We are processing batch {index} to {index + sample_size_per_iteration} :) \n')
            prompt_list = prompt_list_total[index:index + sample_size_per_iteration]
            answer_list = answer_list_total[index:index + sample_size_per_iteration]
            answer_gold_lbl = answer_gold_lbl_total[index:index + sample_size_per_iteration]

            pred, reason_cache = pipeline(add_sys_prompt, batch_size, model_obj)
            log_output = list(zip(prompt_list, answer_list, answer_gold_lbl, pred, reason_cache))
            log_name = f'{index}-{index + sample_size_per_iteration}'
            ReadFile.write_to_csv(log_output, log_name, sys_prompt_dict[gold_label_choice])
            conf_matrx(answer_gold_lbl, pred)

        except Exception as e:
            print(f"\n Error at index {index} to {index + sample_size_per_iteration}: {e}")
            failed_indices.append(f'{index}-{index + sample_size_per_iteration}')
            if "out of memory" in str(e).lower():
                torch.cuda.empty_cache()
                print(" #### CUDA cache cleared. Attempting next batch... ### ")
            continue
    ReadFile.log_error_txt(failed_indices)

