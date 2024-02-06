import random
from .common import get_config_randoms
from nodes import LoraLoader, CheckpointLoaderSimple
from PIL import Image, ImageOps
import numpy as np
import torch
import os
from folder_paths import folder_names_and_paths, get_folder_paths
from comfy.sd import load_checkpoint_guess_config


class BaseNode:
    def __init__(self):
        pass
    FUNCTION = "func"
    REQUIRED = {}
    OPTIONAL = None
    HIDDEN = None
    @classmethod    
    def INPUT_TYPES(s):
        types = {"required": s.REQUIRED}
        if s.OPTIONAL:
            types["optional"] = s.OPTIONAL
        if s.HIDDEN:
            types["hidden"] = s.HIDDEN
        return types
    RETURN_TYPES = ()
    RETURN_NAMES = ()

class classproperty(object):
    def __init__(self, f):
        self.f = f
    def __get__(self, obj, owner):
        return self.f(owner)
    
class SeedContext():
    """
    Context Manager to allow one or more random numbers to be generated, optionally using a specified seed, 
    without changing the random number sequence for other code.
    """
    def __init__(self, seed=None):
        self.seed = seed
    def __enter__(self):
        self.state = random.getstate()
        if self.seed:
            random.seed(self.seed)
    def __exit__(self, exc_type, exc_val, exc_tb):
        random.setstate(self.state)

  
class SystematicBase(BaseNode):
    CATEGORY = "randoms"
    LAST = None
    def IS_CHANGED(self, **kwargs):
        return float("NaN")
    
    def func(self, minimum, maximum, step, restart):
        if self.LAST is None or restart=='yes': 
            self.LAST = minimum
        else:
            self.LAST += step
            if self.LAST > maximum: self.LAST = minimum
            if self.LAST < minimum: self.LAST = maximum
        return (self.LAST,)

class SystematicInt(SystematicBase):
    RETURN_TYPES = ("INT",)
    REQUIRED = {"minimum": ("INT", {"default": 0}), 
                "maximum": ("INT", {"default": 100}), 
                "step": ("INT", {"default":1}),
                "restart": (["no","yes"], ) }


class RandomBase(BaseNode):
    CATEGORY = "randoms"
    def IS_CHANGED(self, **kwargs):
        return random.random()

def SEED_INPUT():
    with SeedContext(None):
        return ("INT",{"default": random.randint(1,999999999), "min": 0, "max": 0xffffffffffffffff})

class RandomFloat(RandomBase):
    RETURN_NAMES = ("random_float",)
    REQUIRED = { 
                "minimum": ("FLOAT", {"default": 0.0}), 
                "maximum": ("FLOAT", {"default": 1.0}), 
                "seed": SEED_INPUT(),
    }
    OPTIONAL = { "decimal_places": ("INT", {"default": 10, "min":1, "max":20}), }
    RETURN_TYPES = ("FLOAT",)
    def func(self, minimum, maximum, seed, decimal_places=10):
        with SeedContext(seed):
            rand = round(random.uniform(minimum, maximum), decimal_places)
        return (rand,)

class RandomInt(RandomBase):
    RETURN_NAMES = ("random_int",)
    REQUIRED = { 
                "minimum": ("INT", {"default": 0}), 
                "maximum": ("INT", {"default": 99999999}), 
                "seed": SEED_INPUT(),
            }
    RETURN_TYPES = ("INT",)
    def func(self, minimum, maximum, seed):
        with SeedContext(seed):
            rand = random.randint(minimum, maximum)
        return (rand,)

def from_list(seed, list, index):
    if seed!=0:
        with SeedContext(seed):
            return (random.choice(list), index)
    else:
        index = (index+1)%len(list)
        return (list[index], index)

class LoadRandomLora(RandomBase, LoraLoader):
    @classmethod
    def INPUT_TYPES(s):
        i = LoraLoader.INPUT_TYPES()
        i['required'].pop('lora_name')
        i['required']['seed'] = SEED_INPUT()
        i['optional'] = s.OPTIONAL
        return i
    RETURN_TYPES = ("MODEL", "CLIP", "STRING",)
    RETURN_NAMES = ("model", "clip", "lora_name",)
    OPTIONAL = {"lora_name": ("STRING", {"default":""})}

    def __init__(self):
        self.systematic_index = -1
        LoraLoader.__init__(self)

    def func(self, model, clip, strength_model, strength_clip, seed, lora_name=""):
        loras = get_config_randoms('lora_names', exception_if_missing_or_empty=True)
        if lora_name=="":
            lora_name, self.systematic_index = from_list(seed, loras, self.systematic_index)
        lora_name = lora_name if '.' in lora_name else lora_name + ".safetensors" 
        return self.load_lora(model, clip, lora_name, strength_model, strength_clip) + (lora_name,)

class KeepForRandomBase(RandomBase):
    @classmethod
    def INPUT_TYPES(s):
        return {"required": { "seed": SEED_INPUT(), "keep_for": ("INT", {"default": 1, "min":1, "max":100}) } }
    
    def __init__(self):
        self.since_last_change = 0
        self.result = None

    def func(self, seed, keep_for, **kwargs):
        self.since_last_change += 1
        if self.since_last_change >= keep_for or self.result is None:
            self.since_last_change = 0
            with SeedContext(seed):
                self.result = self.func_(**kwargs)
        return self.result

class LoadRandomCheckpoint(KeepForRandomBase, CheckpointLoaderSimple):
    RETURN_TYPES = ("MODEL", "CLIP", "VAE", "STRING",)
    RETURN_NAMES = ("model", "CLIP", "VAE", "ckpt_name",)

    def func_(self):
        fnap = folder_names_and_paths["checkpoints"]
        options = set()
        for folder in fnap[0]:
            random_folder = os.path.join(folder, "random")
            if os.path.exists(random_folder):
                for file in os.listdir(random_folder):
                    if os.path.splitext(file)[1] in fnap[1]:
                        options.add(os.path.join(random_folder,file))

        ckpt_path = random.choice(list(options))

        out = load_checkpoint_guess_config(ckpt_path, output_vae=True, output_clip=True, embedding_directory=get_folder_paths("embeddings"))
        return out[:3] + (os.path.splitext(os.path.split(ckpt_path)[1])[0],)

class LoadRandomImage(RandomBase):
    def __init__(self):
        self.systematic_index = -1
    REQUIRED = { "folder": ("STRING", {} ), 
                "mode": ( ["random", "iterative"], {}) }
    OPTIONAL = { "seed": ("INT", {"default":0}) }
    RETURN_TYPES = ("IMAGE","STRING",)
    RETURN_NAMES = ("image","filepath",)

    def get_filenames(self, folder):
        image_extensions = get_config_randoms('image_extensions', exception_if_missing_or_empty=True)
        def is_image_filename(filename):
            split = os.path.splitext(filename)
            return len(split)>0 and split[1] in image_extensions
        files = [file for file in os.listdir(folder) if is_image_filename(file)]
        if len(files)==0:
            raise Exception(f"No files matching {image_extensions} in {folder}")
        return files

    def func(self, folder, mode, seed=0):
        seed = seed if mode=="random" else 0
        filename, self.systematic_index = from_list(seed, self.get_filenames(folder), self.systematic_index)
        filepath = os.path.join(folder, filename)
        i = Image.open(filepath)
        i = ImageOps.exif_transpose(i)
        image = i.convert("RGB")
        image = np.array(image).astype(np.float32) / 255.0
        image = torch.from_numpy(image)[None,]
        return (image, filepath, )
