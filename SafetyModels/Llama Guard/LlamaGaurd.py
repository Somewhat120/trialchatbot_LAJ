#!/usr/bin/env python3
# python3 your_script_name.py --settings settings.json

#*****************************
# source llama_env/bin/activate
# python3 your_script_name.py --settings settings.json
"""
The prompt may contain a file, e.g.
[/home/dima/Data/MimicIII/Discharge/Text/160090_discharge.txt]. Summarize!
"""
from importlib.resources import contents
from os import system

from huggingface_hub import login
login("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")

import transformers, torch, os, argparse, ReadFile, time
from transformers import AutoTokenizer, AutoModelForCausalLM, BitsAndBytesConfig
from tqdm import tqdm
from sklearn import metrics
from sklearn.metrics import confusion_matrix
from collections import Counter
import re


harm_word_str = r'\b(unsafe|harmful)\b'
safe_word_str = r'\bSafe\b'

HARM_PATTERN = re.compile(harm_word_str, re.IGNORECASE)
SAFE_PATTERN = re.compile(safe_word_str, re.IGNORECASE)

# suppress tensorflow warnings
os.environ['TF_CPP_MIN_LOG_LEVEL'] = '2'
os.environ['TF_ENABLE_ONEDNN_OPTS'] = '0'
from transformers import logging
# Suppress only warnings
logging.set_verbosity_error()
# speeding up deep learning on CPUs

def main(settings_file, sys_prompt):
    """Chat with Llama"""
    start_time = time.time()
    settings = ReadFile.read_json_file(settings_file)

    model_id = "meta-llama/Meta-Llama-Guard-2-8B"

    quant_config = BitsAndBytesConfig(
        load_in_4bit=True,
        bnb_4bit_compute_dtype=torch.bfloat16,  # Optimized for H100
        bnb_4bit_use_double_quant=True,
        bnb_4bit_quant_type="nf4"
    )

    tokenizer = AutoTokenizer.from_pretrained(model_id)

    model = AutoModelForCausalLM.from_pretrained(
        model_id,
        quantization_config=quant_config,
        device_map="auto",
        torch_dtype=torch.bfloat16
    )

    generator = transformers.pipeline(
        task='text-generation',
        model=model,
        tokenizer=tokenizer,
        device_map="auto",
        pad_token_id=tokenizer.eos_token_id
    )

    data = ReadFile.read_xlsx('Full_Data.xlsx')
    prompt_list = data['question'].tolist()
    answer_list = data['answer'].tolist()
    answer_label_list = data[gold_label_choice_decoder[gold_label_choice]].tolist()

    pred_label_list = []

    for i in tqdm(range(len(prompt_list))):
        conversation = []
        prompt = prompt_list[i]
        answer = answer_list[i]

        if sys_prompt:
            user_input = user_prompt.replace("{question}", prompt).replace("{answer}",answer)
            user_input = system_prompt_content + "\n" + user_input
        else:
            user_input = user_prompt.replace("{question}", prompt).replace("{answer}", answer)

        conversation.append({'role': 'user', 'content': user_input})
        output = generator(
            conversation,
            do_sample=settings['do_sample'],
            temperature=settings['temperature'],
            top_p=settings['top_p'],
            max_new_tokens=settings['max_new_tokens'])
        conversation = output[0]['generated_text']
        result = conversation[-1]['content']
        pred_H_S_lbl = extract_label_anywhere(result) # expected vals: Harmful, Safe
        pred_label_list.append(pred_H_S_lbl)

    pred_label_list_decoded = [pred_lbl_decode[x] for x in pred_label_list]
    metrics_calculation(answer_label_list, pred_label_list_decoded)
    list_tuple = list(zip(prompt_list, answer_list, answer_label_list,pred_label_list_decoded))
    ReadFile.write_to_csv(list_tuple, sys_prompt_dict[gold_label_choice])
    total_time = time.time() - start_time
    formatted = time.strftime("%H:%M:%S", time.gmtime(total_time))

    with open('time_log.txt', 'a') as time_file:
        time_file.write(f"Prompting time for {settings['model_path'].split('/')[-1]} model: {formatted}" + '\n')




def metrics_calculation(y_true,y_pred):
    print(y_pred)
    print(y_true)
    print(50*'*')
    gold_label_freq = Counter(y_true)
    precision = metrics.precision_score(y_true, y_pred, average='binary', pos_label=1)
    recall = metrics.recall_score(y_true, y_pred, average='binary', pos_label=1)
    accuracy = metrics.accuracy_score(y_true, y_pred)
    f1_score = metrics.f1_score(y_true, y_pred, average='binary', pos_label=1)
    specificity = metrics.recall_score(y_true, y_pred, pos_label=0)
    MCC = metrics.matthews_corrcoef(y_true, y_pred)
    balanced_accuracy = metrics.balanced_accuracy_score(y_true, y_pred)
    cm = confusion_matrix(y_true, y_pred)

    with open('metrics_results.txt', 'w') as f:
        f.write(f"""
                # Samples in gold & pred: {len(y_pred)}, {len(y_true)}\n
                label frequency: {gold_label_freq}\n
                Precision: {precision}\n
                Recall: {recall}\n
                Accuracy: {accuracy}\n
                F1-score: {f1_score}\n
                Specificity: {specificity}\n
                MCC: {MCC}\n
                Balanced Accuracy: {balanced_accuracy}\n
                Confusion Matrix: {cm}\n""")


def extract_label_anywhere(text: str) -> str | None:
    if HARM_PATTERN.search(text):
        return "Harmful"
    if SAFE_PATTERN.search(text):
        return "Safe"
    print(f"Warning: Could not parse text: {text[:50]}...")
    return None


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        '--settings',
        type=str,
        help='LLM configuration file',
        default='settings.json')
    args = parser.parse_args()

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
        user_prompt = data["instruction_template"]
    else:
        sys_prompt = False
        print("\n You decided to continue with **NO** sys_prompt. \n")


    pred_lbl_decode = {'Safe':0, 'Harmful':1}
    main(args.settings, sys_prompt)