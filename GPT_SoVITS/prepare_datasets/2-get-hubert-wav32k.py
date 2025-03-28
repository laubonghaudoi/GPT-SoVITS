"""
Step 2 of data preparation: Extract HuBERT features from the audio files,
and resample the audio to 32kHz and saving it.
"""

import os
import shutil
import sys
import traceback
from time import time as ttime

import librosa
import numpy as np
import torch
from feature_extractor import cnhubert
from scipy.io import wavfile
from tqdm import tqdm

from tools.my_utils import clean_path, load_audio

inp_text = os.environ.get("inp_text")
inp_wav_dir = os.environ.get("inp_wav_dir")
exp_name = os.environ.get("exp_name")
i_part = os.environ.get("i_part")
all_parts = os.environ.get("all_parts")
if "_CUDA_VISIBLE_DEVICES" in os.environ:
    os.environ["CUDA_VISIBLE_DEVICES"] = os.environ["_CUDA_VISIBLE_DEVICES"]

opt_dir = os.environ.get("opt_dir")
cnhubert.cnhubert_base_path = os.environ.get("cnhubert_base_dir")

is_half = eval(os.environ.get("is_half", "True")) and torch.cuda.is_available()


now_dir = os.getcwd()
sys.path.append(now_dir)


# from config import cnhubert_base_path
# cnhubert.cnhubert_base_path=cnhubert_base_path
# inp_text=sys.argv[1]
# inp_wav_dir=sys.argv[2]
# exp_name=sys.argv[3]
# i_part=sys.argv[4]
# all_parts=sys.argv[5]
# os.environ["CUDA_VISIBLE_DEVICES"]=sys.argv[6]
# cnhubert.cnhubert_base_path=sys.argv[7]
# opt_dir="/data/docker/liujing04/gpt-vits/fine_tune_dataset/%s"%exp_name

def my_save(fea, path):  # fix issue: torch.save doesn't support chinese path
    dir = os.path.dirname(path)
    name = os.path.basename(path)
    # tmp_path="%s/%s%s.pth"%(dir,ttime(),i_part)
    tmp_path = f"{ttime()}{i_part}.pth"
    torch.save(fea, tmp_path)
    shutil.move(tmp_path, f"{dir}/{name}")


hubert_dir = "%s/4-cnhubert" % (opt_dir)
wav32dir = "%s/5-wav32k" % (opt_dir)
os.makedirs(opt_dir, exist_ok=True)
os.makedirs(hubert_dir, exist_ok=True)
os.makedirs(wav32dir, exist_ok=True)

maxx = 0.95
alpha = 0.5
if torch.cuda.is_available():
    device = "cuda:0"
# elif torch.backends.mps.is_available():
#     device = "mps"
else:
    device = "cpu"


model = cnhubert.get_model()
# is_half=False
if (is_half == True):
    model = model.half().to(device)
else:
    model = model.to(device)

nan_fails = []


def name2go(wav_name, wav_path):
    """
    Extract HuBERT features from the audio files, and resample the audio to 32kHz and saving it.
    """
    # Skip if the file already exists
    hubert_path = "%s/%s.pt" % (hubert_dir, wav_name)
    if (os.path.exists(hubert_path)):
        return

    # Load the audio file in 32kHz sampling rate
    tmp_audio = load_audio(wav_path, 32000)

    # Check the maximum amplitude of the audio file
    tmp_max = np.abs(tmp_audio).max()
    # Skip if the maximum amplitude is too high (volume is too loud)
    if tmp_max > 2.2:
        print(f"{wav_name}-filtered,{tmp_max}")
        return
    # Normalize the audio
    tmp_audio32 = (tmp_audio / tmp_max * (maxx * alpha * 32768)) + ((1 - alpha) * 32768) * tmp_audio
    tmp_audio32b = (tmp_audio / tmp_max * (maxx * alpha * 1145.14)) + ((1 - alpha) * 1145.14) * tmp_audio
    tmp_audio = librosa.resample(
        tmp_audio32b, orig_sr=32000, target_sr=16000
    )  # 不是重采样问题
    tensor_wav16 = torch.from_numpy(tmp_audio)

    # if half-precision is enabled, convert the tensor to half-precision
    if is_half:
        tensor_wav16 = tensor_wav16.half().to(device)
    else:
        tensor_wav16 = tensor_wav16.to(device)

    # Extract HuBERT features from the audio file
    ssl = model.model(tensor_wav16.unsqueeze(0))["last_hidden_state"].transpose(1, 2).cpu()  # torch.Size([1, 768, 215])

    if np.isnan(ssl.detach().numpy()).sum() != 0:
        nan_fails.append((wav_name, wav_path))
        print(f"nan filtered:{wav_name}")
        return
    wavfile.write(
        f"{wav32dir}/{wav_name}",
        32000,
        tmp_audio32.astype("int16"),
    )
    my_save(ssl, hubert_path)


with open(inp_text, "r", encoding="utf8")as f:
    lines = f.read().strip("\n").split("\n")

for line in tqdm(lines[int(i_part)::int(all_parts)]):
    try:
        # wav_name,text=line.split("\t")
        wav_name, spk_name, language, text = line.split("|")
        wav_name = clean_path(wav_name)
        if (inp_wav_dir != "" and inp_wav_dir is not None):
            wav_name = os.path.basename(wav_name)
            wav_path = f"{inp_wav_dir}/{wav_name}"

        else:
            wav_path = wav_name
            wav_name = os.path.basename(wav_name)
        name2go(wav_name, wav_path)
    except:
        print(line, traceback.format_exc())

if (len(nan_fails) > 0 and is_half):
    is_half = False
    model = model.float()
    for wav in nan_fails:
        try:
            name2go(wav[0], wav[1])
        except:
            print(wav_name, traceback.format_exc())
