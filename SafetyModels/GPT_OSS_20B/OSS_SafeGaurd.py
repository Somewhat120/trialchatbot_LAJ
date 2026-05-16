import os
import warnings
# This must come BEFORE 'import torch'
os.environ["PYTORCH_ALLOC_CONF"] = "expandable_segments:True"
# Set this environment variable specifically for the OpenMP conflict
os.environ["KMP_DUPLICATE_LIB_OK"] = "TRUE"
warnings.filterwarnings("ignore", category=UserWarning)
warnings.filterwarnings("ignore", message=".*PYTORCH_CUDA_ALLOC_CONF.*")
import ReadFile
import json
from tqdm import tqdm
from sklearn.metrics import confusion_matrix
from collections import Counter
import torch
import re
import gc
from transformers import AutoTokenizer, AutoModelForCausalLM, BitsAndBytesConfig
######################################################
#           ATTENTION       #
MODEL_ID = "openai/gpt-oss-safeguard-20b"
#######################################################

class HarmonyClassifier:
    def __init__(self, model_path, quantize_8bit=False, quantize_4bit=False):
        self.tokenizer = AutoTokenizer.from_pretrained(model_path)

        quant_config = None
        if quantize_8bit:
            quant_config = BitsAndBytesConfig(load_in_8bit=True)
        elif quantize_4bit:
            quant_config = BitsAndBytesConfig(
                load_in_4bit=True,
                bnb_4bit_compute_dtype=torch.bfloat16,
                bnb_4bit_use_double_quant=True,
                bnb_4bit_quant_type="nf4"
            )

        kwargs = {
            "pretrained_model_name_or_path": model_path,
            "device_map": "auto",
            "trust_remote_code": True,
            "low_cpu_mem_usage": True,
        }

        if quant_config:
            kwargs["quantization_config"] = quant_config
        else:
            kwargs["torch_dtype"] = torch.bfloat16



        self.model = AutoModelForCausalLM.from_pretrained(**kwargs)
        self.tokenizer.padding_side = "left"
        if self.tokenizer.pad_token is None:
            self.tokenizer.pad_token = self.tokenizer.eos_token

    def generate(self, inputs):
        with torch.no_grad():
            outputs = self.model.generate(
                **inputs,
                max_new_tokens=3500,
                pad_token_id=self.tokenizer.pad_token_id,
                do_sample=False,
                repetition_penalty=1.15,
            )
        input_length = inputs.input_ids.shape[1]
        return self.tokenizer.batch_decode(outputs[:, input_length:], skip_special_tokens=False)


def parse_harmony_output(raw_text, format):
    final_pattern = r"<\|start\|>assistant(?:<\|channel\|>|\.)final<\|message\|>(.*?)(?:<\|end\|>|<\|return\|>|$)"
    match = re.search(final_pattern, raw_text, re.DOTALL)
    clean_label = 0
    reasoning = ""
    if match:
        extracted_text = match.group(1).strip()
        if format == 'json':
            start_idx = extracted_text.find('{')
            end_idx = extracted_text.rfind('}')

            if start_idx != -1 and end_idx != -1:
                json_string = extracted_text[start_idx: end_idx + 1]
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
            clean_label = extracted_text.replace("*", "").strip()
            reasoning = 'None'

        return clean_label, reasoning

    else:
        raise ValueError("Regex pattern did not match raw text.")

def pipeline(add_sys_prompt: bool, batch_size:int, model_object:HarmonyClassifier) -> list:
    pred_lbl_list = []
    reasoning_cache_list = []

    for indx in range(0, len(prompt_list), batch_size):
        prompt_batch = prompt_list[indx: indx + batch_size]
        answer_batch = answer_list[indx: indx + batch_size]
        batch_chunk = list(zip(prompt_batch, answer_batch))
        wrapped_batch = []
        for sample in batch_chunk:
            user_prompt = user_prompt_content.replace("{question}", sample[0]).replace("{answer}",sample[1])
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

            full_prompt_string = model_object.tokenizer.apply_chat_template(
                messages,
                tokenize=False,
                add_generation_prompt=True
            )

            wrapped_batch.append(full_prompt_string)

        inputs = model_object.tokenizer(
            wrapped_batch,
            return_tensors="pt",
            padding=True,
            truncation=True
        ).to("cuda")

        output = model_object.generate(inputs)
        for result in output:
            label, reason_ = parse_harmony_output(result, format)
            pred_lbl_list.append(label)
            if reasoning_access:
                reasoning_cache_list.append(reason_)

        del inputs, output, wrapped_batch
        torch.cuda.empty_cache()
        gc.collect()
    return pred_lbl_list, reasoning_cache_list

def conf_matrx(y_true, y_pred) -> str:
    # print('********We Got into Confusion Matrix******')
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
    print(f"WARNING: You chose to evaluate {gold_label_choice_decoder[gold_label_choice]}\n")

    sys_prompt_dict = {1: '1_critical_harm_v1', 2: '2_individualized_advice_v1',
                       3: '3_persuasion_v1'}
    sys_prompt_decision = input("Do you wish to test with your sys_prompt y/n ? ")
    if sys_prompt_decision == "y":
        sys_prompt = True
        print(f"You decided to apply your chosen sys_prompt. \n CHOICE: {sys_prompt_dict[gold_label_choice]}\n")
        data = ReadFile.read_json_file(f"{sys_prompt_dict[gold_label_choice]}.json")
        system_prompt_content = data["system_role"]
        user_prompt_content = data["instruction_template"]
        print(system_prompt_content + '\n')
    else:
        sys_prompt = False
        print("You decided to continue with **NO** sys_prompt. \n")

    user_choice = input("Do you wish to see the CoT ? y/n: \n")
    if user_choice == "y":
        reasoning_access = True
        print("WARNING: You chose to access to model's CoT.\n")
    else:
        print("WARNING: You chose **NOT** to access to model's CoT.\n")


    thinking_level_choice = input("What level of thinking do you expect ? l/m/h: \n")
    if thinking_level_choice == "l":
        thinking_level = 'low'
    elif thinking_level_choice == "m":
        thinking_level = 'medium'
    elif thinking_level_choice == "h":
        thinking_level = 'high'
    else:
        thinking_level = 'medium'
    print(f"WARNING: You chose {thinking_level} level thinking approachGPT.\n")


    output_format_decision = input("Do you expect json (CoT + Label) output format? y/n: \n")
    if output_format_decision == "y":
        format = 'json'
        print("WARNING: You chose json format output scenario!\n")
    else:
        format = 'token'
        print("WARNING: You chose token format output scenario!\n")


    model_obj = HarmonyClassifier(model_path=MODEL_ID)

    gold_lbl_decode = {0: 0, 1: 1, 2: 1, 3: 1}
    input_data = ReadFile.read_xlsx(data_name)
    # input_data = ReadFile.read_csv(data_name)
    prompt_list_total = input_data['question'].tolist()#[80:105]
    answer_list_total = input_data['answer'].tolist()#[80:105]
    answer_gold_lbl_total_en = input_data[gold_label_choice_decoder[gold_label_choice]].tolist()#[80:105]
    answer_gold_lbl_total = [gold_lbl_decode[x] for x in answer_gold_lbl_total_en]

    failed_indices = []
    for index in tqdm(range(0, len(prompt_list_total), sample_size_per_iteration)):
        try:
            print(f'We are processing batch {index} to {index + sample_size_per_iteration} :) \n')
            prompt_list = prompt_list_total[index:index + sample_size_per_iteration]
            answer_list = answer_list_total[index:index + sample_size_per_iteration]
            answer_gold_lbl = answer_gold_lbl_total[index:index + sample_size_per_iteration]

            pred, reason_cache = pipeline(add_sys_prompt, batch_size, model_obj)
            log_output = list(zip(prompt_list, answer_list, answer_gold_lbl, pred, reason_cache))
            log_name = f'{index}-{index + sample_size_per_iteration}'
            ReadFile.write_to_csv(log_output, log_name, sys_prompt_dict[gold_label_choice])
            conf_matrx(answer_gold_lbl, pred)

        except Exception as e:
            print(f"Error at index {index} to {index + sample_size_per_iteration}: {e}\n")
            failed_indices.append(f'{index}-{index + sample_size_per_iteration}')
            if "out of memory" in str(e).lower():
                torch.cuda.empty_cache()
                print("#### CUDA cache cleared. Attempting next batch... ### \n")
            continue
    ReadFile.log_error_txt(failed_indices)




