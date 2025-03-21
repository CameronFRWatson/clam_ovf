import torch
import torch.nn as nn
import os
import time
import h5py
import openslide
import timm
import argparse
from datetime import datetime


from datasets.dataset_h5 import Dataset_All_Bags, Whole_Slide_Bag_FP
from torch.utils.data import DataLoader
from models.resnet_custom import resnet18_baseline,resnet50_baseline
from utils.utils import collate_features
from utils.file_utils import save_hdf5
from models.HIPT_4K.hipt_4k import HIPT_4K
from models.HIPT_4K.hipt_model_utils import eval_transforms
from models.lunit import resnet50_lunit, vit_small_lunit
from models import hibou
from models.GPFM import vision_transformer as vits
from transformers import AutoImageProcessor, ViTModel, AutoModel 

import torchvision
import torch
from torchvision import transforms
import torchstain
#from torch_staintools.normalizer import NormalizerBuilder ## disabling this temporarily which will break Vahadane

device = torch.device('cuda') if torch.cuda.is_available() else torch.device('cpu')
print("torch device:", device, "\n")

def compute_w_loader(file_path, output_path, wsi, model,
        batch_size = 8, verbose = 0, print_every=20, pretrained=True, 
        custom_downsample=2, target_patch_size=-1):
        """
        args:
                file_path: directory of bag (.h5 file)
                output_path: directory to save computed features (.h5 file)
                model: pytorch model
                batch_size: batch_size for computing features in batches
                verbose: level of feedback
                pretrained: use weights pretrained on imagenet
                custom_downsample: custom defined downscale factor of image patches
                target_patch_size: custom defined, rescaled image size before embedding
        """
        
        if args.use_transforms=='macenko':
            class MacenkoNormalisation:
                def __init__(self):
                    self.normalizer = torchstain.normalizers.MacenkoNormalizer(backend='torch')
                    self.failures=0

                def __call__(self,image):
                    try:
                        norm, _, _ = self.normalizer.normalize(I=image, stains=False)
                        norm = norm.permute(2, 0, 1)/255
                    except:
                        norm=image/255
                        self.failures=self.failures+1
                        print("failed patches: ",self.failures)
                    return(norm)

            t = transforms.Compose(
                [transforms.ToTensor(),
                transforms.Lambda(lambda x: x*255),
                MacenkoNormalisation()])
            dataset = Whole_Slide_Bag_FP(file_path=file_path, wsi=wsi, custom_transforms=t, pretrained=pretrained,
                custom_downsample=custom_downsample, target_patch_size=target_patch_size)


        elif args.use_transforms=='reinhard':
            class ReinhardNormalisation:
                def __init__(self):
                    self.normalizer = torchstain.normalizers.ReinhardNormalizer(backend='torch')
                    ## targets calculated from a specific patch in 494821.svs
                    self.normalizer.target_means = torch.tensor([79.2929, 11.2809, -5.9533])
                    self.normalizer.target_stds = torch.tensor([17.3957,  8.6891, 10.5019])

                def __call__(self,image):
                    norm = self.normalizer.normalize(I=image)
                    norm = norm.permute(2, 0, 1)/255
                    return(norm)
            
            t = transforms.Compose(
                [transforms.ToTensor(),
                transforms.Lambda(lambda x: x*255),
                ReinhardNormalisation()])
            dataset = Whole_Slide_Bag_FP(file_path=file_path, wsi=wsi, custom_transforms=t, pretrained=pretrained,
                custom_downsample=custom_downsample, target_patch_size=target_patch_size)
        
        elif args.use_transforms=='vahadane':
            class VahadaneNormalisation:
                def __init__(self):
                    self.normalizer = NormalizerBuilder.build('vahadane', concentration_method='ls').to(device) 
                    ## targets calculated from the first patch in 530725.svs
                    self.normalizer.stain_matrix_target = torch.tensor([[[0.5440, 0.7058, 0.4538],[0.4231, 0.7917, 0.4406]]]) 
                    self.normalizer.maxC_target = torch.tensor([[2.2052, 1.0442]])
                    
                def __call__(self,image):
                    norm = self.normalizer.transform(image.unsqueeze(0)).squeeze(0)
                    return(norm)

            t = transforms.Compose(
                [transforms.ToTensor(),
                VahadaneNormalisation()])
            dataset = Whole_Slide_Bag_FP(file_path=file_path, wsi=wsi, custom_transforms=t, pretrained=pretrained,
                custom_downsample=custom_downsample, target_patch_size=target_patch_size)

        elif args.use_transforms=='colourjitter':
            ## the colour augmentations used by AIMlab - https://github.com/AIMLab-UBC/MIDL2020/blob/5b1874b1d1b6d69785ca7ad259dc50b6180f9fb6/config.py#L106
            t = transforms.Compose(
                [transforms.ColorJitter(64.0 / 255, 0.75, 0.25, 0.04),
                transforms.ToTensor(),])
            dataset = Whole_Slide_Bag_FP(file_path=file_path, wsi=wsi, custom_transforms=t, pretrained=pretrained,custom_downsample=custom_downsample, target_patch_size=target_patch_size)

        elif args.use_transforms=='colourjitternorm':
            ## as above but using imagenet normalisation at end like normal - forgot this originally
            t = transforms.Compose(
                [transforms.ColorJitter(64.0 / 255, 0.75, 0.25, 0.04),
                transforms.ToTensor(),
                transforms.Normalize(mean = (0.485, 0.456, 0.406), std=(0.229, 0.224, 0.225))])
            dataset = Whole_Slide_Bag_FP(file_path=file_path, wsi=wsi, custom_transforms=t, pretrained=pretrained,custom_downsample=custom_downsample, target_patch_size=target_patch_size)

        elif args.use_transforms=='all':
            t = transforms.Compose(
                [transforms.ToTensor(),
                transforms.RandomHorizontalFlip(p=0.5),
                transforms.RandomVerticalFlip(p=0.5),
                transforms.RandomAffine(degrees=90,translate=(0.1,0.1), scale=(0.9,1.1),shear=0.1),
                transforms.ColorJitter(brightness=0.1, contrast=0.1, saturation=0.1, hue=0.1),
                transforms.Normalize(mean = (0.485, 0.456, 0.406), std=(0.229, 0.224, 0.225))])
            dataset = Whole_Slide_Bag_FP(file_path=file_path, wsi=wsi, custom_transforms=t, pretrained=pretrained,
                custom_downsample=custom_downsample, target_patch_size=target_patch_size)
        
        elif args.use_transforms=='spatial':
            t = transforms.Compose(
                [transforms.ToTensor(),
                transforms.RandomHorizontalFlip(p=0.5),
                transforms.RandomVerticalFlip(p=0.5),
                transforms.RandomAffine(degrees=90,translate=(0.1,0.1), scale=(0.9,1.1),shear=0.1),
                transforms.Normalize(mean = (0.485, 0.456, 0.406), std=(0.229, 0.224, 0.225))])
            dataset = Whole_Slide_Bag_FP(file_path=file_path, wsi=wsi, custom_transforms=t, pretrained=pretrained,
                custom_downsample=custom_downsample, target_patch_size=target_patch_size)
        
        elif args.use_transforms=='HIPT':
            t = eval_transforms()
            dataset = Whole_Slide_Bag_FP(file_path=file_path, wsi=wsi, custom_transforms=t, pretrained=pretrained,
                custom_downsample=custom_downsample, target_patch_size=target_patch_size)
        
        elif args.use_transforms=='HIPT_blur':
            t =  transforms.Compose(
                    [transforms.GaussianBlur(kernel_size=(1, 3), sigma=(7, 9)),
                    eval_transforms()
                    ])
            dataset = Whole_Slide_Bag_FP(file_path=file_path, wsi=wsi, custom_transforms=t, pretrained=pretrained,
                custom_downsample=custom_downsample, target_patch_size=target_patch_size)

        elif args.use_transforms=='HIPT_wang':
        ## augmentations from the baseline ATEC23 paper
            t = transforms.Compose(
                    [transforms.RandomHorizontalFlip(p=0.5),
                    transforms.RandomVerticalFlip(p=0.5),
                    transforms.RandomAffine(degrees=90),
                    transforms.ColorJitter(brightness=0.125, contrast=0.2, saturation=0.2),
                    eval_transforms()])
            dataset = Whole_Slide_Bag_FP(file_path=file_path, wsi=wsi, custom_transforms=t, pretrained=pretrained,
                custom_downsample=custom_downsample, target_patch_size=target_patch_size)

        elif args.use_transforms=='HIPT_augment_colour':
            ## same as HIPT_augment but no affine
            t = transforms.Compose(
                    [transforms.RandomHorizontalFlip(p=0.5),
                    transforms.RandomVerticalFlip(p=0.5),
                    transforms.ColorJitter(brightness=0.2, contrast=0.2, saturation=0.2, hue=0.2),
                    eval_transforms()])
            dataset = Whole_Slide_Bag_FP(file_path=file_path, wsi=wsi, custom_transforms=t, pretrained=pretrained,
                custom_downsample=custom_downsample, target_patch_size=target_patch_size)
        
        elif args.use_transforms=='HIPT_augment':
            t = transforms.Compose(
                    [transforms.RandomHorizontalFlip(p=0.5),
                    transforms.RandomVerticalFlip(p=0.5),
                    transforms.RandomAffine(degrees=5,translate=(0.025,0.025), scale=(0.975,1.025),shear=0.025),
                    transforms.ColorJitter(brightness=0.2, contrast=0.2, saturation=0.2, hue=0.2),
                    eval_transforms()])
            dataset = Whole_Slide_Bag_FP(file_path=file_path, wsi=wsi, custom_transforms=t, pretrained=pretrained,
                custom_downsample=custom_downsample, target_patch_size=target_patch_size)
        
        elif args.use_transforms=='HIPT_augment01':
            t = transforms.Compose(
                    [transforms.RandomHorizontalFlip(p=0.5),
                    transforms.RandomVerticalFlip(p=0.5),
                    transforms.RandomAffine(degrees=5,translate=(0.025,0.025), scale=(0.975,1.025),shear=0.025),
                    transforms.ColorJitter(brightness=0.1, contrast=0.1, saturation=0.1, hue=0.1),
                    eval_transforms()])
            dataset = Whole_Slide_Bag_FP(file_path=file_path, wsi=wsi, custom_transforms=t, pretrained=pretrained,
                custom_downsample=custom_downsample, target_patch_size=target_patch_size)

        elif args.use_transforms=='histo_resnet18':
            t = transforms.Compose(
                    [transforms.ToTensor(),
                    transforms.Normalize(mean=(0.5, 0.5, 0.5), std=(0.5, 0.5, 0.5))])
            dataset = Whole_Slide_Bag_FP(file_path=file_path, wsi=wsi, custom_transforms=t, pretrained=pretrained,custom_downsample=custom_downsample, target_patch_size=target_patch_size)

        elif args.use_transforms=='histo_resnet18_224':
            t = transforms.Compose(
                    [transforms.Resize(224),
                    transforms.ToTensor(),
                    transforms.Normalize(mean=(0.5, 0.5, 0.5), std=(0.5, 0.5, 0.5))])
            dataset = Whole_Slide_Bag_FP(file_path=file_path, wsi=wsi, custom_transforms=t, pretrained=pretrained,custom_downsample=custom_downsample, target_patch_size=target_patch_size)

        elif args.use_transforms=='resnet50lunit_default':
            t = transforms.Compose(
                    [transforms.ToTensor(),
                    transforms.Normalize(mean=(0.70322989, 0.53606487, 0.66096631), std=(0.21716536, 0.26081574, 0.20723464))])
            dataset = Whole_Slide_Bag_FP(file_path=file_path, wsi=wsi, custom_transforms=t, pretrained=pretrained,custom_downsample=custom_downsample, target_patch_size=target_patch_size)

        elif args.use_transforms=='vitSlunit_default':
            t = transforms.Compose(
                    [transforms.Resize(224),
                    transforms.ToTensor(),
                    transforms.Normalize(mean=(0.70322989, 0.53606487, 0.66096631), std=(0.21716536, 0.26081574, 0.20723464))])
            dataset = Whole_Slide_Bag_FP(file_path=file_path, wsi=wsi, custom_transforms=t, pretrained=pretrained,custom_downsample=custom_downsample, target_patch_size=target_patch_size)

        elif args.use_transforms=='uni_default':
            t = transforms.Compose(
                    [transforms.Resize(224),
                    transforms.ToTensor(),
                    transforms.Normalize(mean=(0.485, 0.456, 0.406), std=(0.229, 0.224, 0.225))])
            dataset = Whole_Slide_Bag_FP(file_path=file_path, wsi=wsi, custom_transforms=t, pretrained=pretrained,custom_downsample=custom_downsample, target_patch_size=target_patch_size)

        elif args.use_transforms=='gigapath_default':
            t = transforms.Compose(
                    [transforms.Resize(256, interpolation=transforms.InterpolationMode.BICUBIC),
                    transforms.CenterCrop(224),
                    transforms.ToTensor(),
                    transforms.Normalize(mean=(0.485, 0.456, 0.406), std=(0.229, 0.224, 0.225))])
            dataset = Whole_Slide_Bag_FP(file_path=file_path, wsi=wsi, custom_transforms=t, pretrained=pretrained,custom_downsample=custom_downsample, target_patch_size=target_patch_size)

        elif args.use_transforms=='gpfm_default':
            t = transforms.Compose(
                    [transforms.Resize((224, 224), interpolation=3),
                    transforms.ToTensor(),
                    transforms.Normalize(mean=(0.485, 0.456, 0.406), std=(0.229, 0.224, 0.225))])
            dataset = Whole_Slide_Bag_FP(file_path=file_path, wsi=wsi, custom_transforms=t, pretrained=pretrained,custom_downsample=custom_downsample, target_patch_size=target_patch_size)


        elif args.use_transforms=='hibou_default':
            t = transforms.Compose(
                    [transforms.Resize((224, 224), interpolation=torchvision.transforms.InterpolationMode.BICUBIC),
                    transforms.CenterCrop((224, 224)),
                    transforms.ToTensor(),
                    transforms.Normalize(mean=[0.7068, 0.5755, 0.7220], std=[0.1950, 0.2316, 0.1816])])
            dataset = Whole_Slide_Bag_FP(file_path=file_path, wsi=wsi, custom_transforms=t, pretrained=pretrained,custom_downsample=custom_downsample, target_patch_size=target_patch_size)

        elif args.use_transforms=='kaiko_default':
            t = transforms.Compose(
                    [transforms.Resize(size=224),
                    transforms.CenterCrop(size=224),
                    transforms.ToTensor(),
                    transforms.Normalize(mean=(0.5, 0.5, 0.5),std=(0.5, 0.5, 0.5))])
            dataset = Whole_Slide_Bag_FP(file_path=file_path, wsi=wsi, custom_transforms=t, pretrained=pretrained,custom_downsample=custom_downsample, target_patch_size=target_patch_size)

        elif args.use_transforms=='optimus_default':
            t = transforms.Compose(
                    [transforms.CenterCrop(size=224),
                    transforms.ToTensor(),
                    transforms.Normalize(mean=(0.707223, 0.578729, 0.703617), std=(0.211883, 0.230117, 0.177517))])
            dataset = Whole_Slide_Bag_FP(file_path=file_path, wsi=wsi, custom_transforms=t, pretrained=pretrained,custom_downsample=custom_downsample, target_patch_size=target_patch_size)
        
        else:
            dataset = Whole_Slide_Bag_FP(file_path=file_path, wsi=wsi, pretrained=pretrained, 
                custom_downsample=custom_downsample, target_patch_size=target_patch_size)
        dataset.update_sample(range(len(dataset)))
        x, y = dataset[0]
        
        kwargs = {'num_workers': 4, 'pin_memory': True} if device.type == "cuda" else {}
        if args.model_type=='levit_128s':
            kwargs = {'num_workers': 16, 'pin_memory': True} if device.type == "cuda" else {}
            tfms=torch.nn.Sequential(transforms.CenterCrop(224))
        elif args.model_type in ['uni', 'vit_l']:
            kwargs = {'num_workers': 4, 'pin_memory': True} if device.type == "cuda" else {}
        elif args.model_type=='HIPT_4K':
            if args.hardware=='DGX':
                kwargs = {'num_workers': 4, 'pin_memory': True} if device.type == "cuda" else {}
            else:
                kwargs = {'num_workers': 1, 'pin_memory': True} if device.type == "cuda" else {}
        loader = DataLoader(dataset=dataset, batch_size=batch_size, **kwargs, collate_fn=collate_features)

        if verbose > 0:
                print('processing {}: total of {} batches'.format(file_path,len(loader)))

        mode = 'w'
        for count, (batch, coords) in enumerate(loader):
                with torch.no_grad():   
                        if count % print_every == 0:
                                print('batch {}/{}, {} files processed'.format(count, len(loader), count * batch_size))
                        batch = batch.to(device, non_blocking=True)
                        
                        if args.model_type=='levit_128s':
                            batch=tfms(batch)
                        
                        features = model(batch)
                        if args.model_type=='phikon':
                            features = features.last_hidden_state[:, 0, :]

                        if args.model_type=='virchow':
                            class_token = features[:, 0]
                            patch_tokens = features[:, 1:]
                            features = torch.cat([class_token, patch_tokens.mean(1)], dim=-1)

                        if args.model_type=='virchow2cls':
                            features = features[:, 0] ##only using CLS tokens is best in Zimmermann 2024 preprint
                        
                        if args.model_type=='hibou_l':
                            features = features.pooler_output


                        features = features.cpu().numpy()
                        asset_dict = {'features': features, 'coords': coords}
                        save_hdf5(output_path, asset_dict, attr_dict= None, mode=mode)
                        mode = 'a'
        
        return output_path


parser = argparse.ArgumentParser(description='Feature Extraction')
parser.add_argument('--data_h5_dir', type=str, default=None)
parser.add_argument('--data_slide_dir', type=str, default=None)
parser.add_argument('--slide_ext', type=str, default= '.svs')
parser.add_argument('--csv_path', type=str, default=None)
parser.add_argument('--feat_dir', type=str, default=None)
parser.add_argument('--batch_size', type=int, default=256)
parser.add_argument('--no_auto_skip', default=False, action='store_true')
parser.add_argument('--print_every', type=int, default=100, help='number of batches to process between print statements')
parser.add_argument('--custom_downsample', type=int, default=1)
parser.add_argument('--target_patch_size', type=int, default=-1)
parser.add_argument('--pretraining_dataset', type=str, choices=['ImageNet','Histo'], default='ImageNet')
parser.add_argument('--model_type', type=str, choices=['resnet18', 'resnet50', 'resnet50lunit', 'vitSlunit', 'densenet121', 'levit_128s', 'HIPT_4K', 'uni', 'vit_l', 'ctranspath', 'provgigapath', 'phikon','gpfm', 'virchow', 'virchow2cls', 'hibou_b', 'hibou_l', 'kaiko_b8', 'optimus'], default='resnet50')
parser.add_argument('--model_weights_path', type=str, default="/mnt/results/Checkpoints/", help="location of pre-trained model, only needed for UNI, HIPT_4K and cTransPath")
parser.add_argument('--use_transforms',type=str,choices=['all', 'HIPT', 'HIPT_blur', 'HIPT_augment', 'HIPT_augment_colour', 'HIPT_wang', 'HIPT_augment01', 'spatial', 'colourjitter', 'colourjitternorm', 'macenko', 'reinhard', 'vahadane', 'none', 'resnet50lunit_default', 'vitSlunit_default', 'uni_default', 'gigapath_default', 'gpfm_default', 'hibou_default', 'kaiko_default', 'optimus_default', 'histo_resnet18', 'histo_resnet18_224'], default='none')
parser.add_argument('--hardware', type=str, default="PC")
parser.add_argument('--graph_patches', type=str, choices=['none','small','big'], default='none')
parser.add_argument('--save_times', default=False, action='store_true')
args = parser.parse_args()


if __name__ == '__main__':

        print('initializing dataset')
        csv_path = args.csv_path
        if csv_path is None:
                raise NotImplementedError

        bags_dataset = Dataset_All_Bags(csv_path)
        
        os.makedirs(args.feat_dir, exist_ok=True)
        os.makedirs(os.path.join(args.feat_dir, 'pt_files'), exist_ok=True)
        os.makedirs(os.path.join(args.feat_dir, 'h5_files'), exist_ok=True)
        dest_files = os.listdir(os.path.join(args.feat_dir, 'pt_files'))
        
        print('loading {} model'.format(args.model_type))
        if args.model_type=='resnet18':
            model = resnet18_baseline(pretrained=True,dataset=args.pretraining_dataset)
            if args.pretraining_dataset=='Histo':
                assert args.use_transforms in ['histo_resnet18','histo_resnet18_224']
        
        elif args.model_type=='resnet50':
            model = resnet50_baseline(pretrained=True,dataset=args.pretraining_dataset)
        
        elif args.model_type=='densenet121':
            model = torchvision.models.densenet121(pretrained=True,num_classes=1024) 
        
        elif args.model_type=='levit_128s':
            model=timm.create_model('levit_256',pretrained=True, num_classes=0)    
        
        elif args.model_type=='uni':
            model = timm.create_model("vit_large_patch16_224", img_size=224, patch_size=16, init_values=1e-5, num_classes=0, dynamic_img_size=True)
            model.load_state_dict(torch.load(os.path.join(args.model_weights_path+"vit_large_patch16_224.dinov2.uni_mass100k/pytorch_model.bin"), map_location="cpu"), strict=True)
            assert args.use_transforms in ["uni_default"]
        
        elif args.model_type == 'resnet50lunit':
             model = resnet50_lunit(pretrained=True, progress=False, key="BT")
             assert args.use_transforms in ["resnet50lunit_default"]

        elif args.model_type == 'vitSlunit':
             model = vit_small_lunit(pretrained=True, progress=False, key="DINO_p8", patch_size=8)
             assert args.use_transforms in ["vitSlunit_default"]

        elif args.model_type =='vit_l':
             model = timm.create_model("vit_large_patch16_224",  num_classes=0,  pretrained = True)
             assert args.use_transforms in ["uni_default"]
        
        elif args.model_type == 'ctranspath':
            from models.ctran import ctranspath
            model = ctranspath()
            model.head = nn.Identity()
            td = torch.load(args.model_weights_path+'ctranspath.pth')
            model.load_state_dict(td['model'], strict=True)
            assert args.use_transforms in ["uni_default"] ## uni and ctranspath have same preprocessing

        elif args.model_type == 'provgigapath':
            print("if not working, remember to input the huggingface token using 'huggingface-cli login' command")
            model = timm.create_model("hf_hub:prov-gigapath/prov-gigapath", pretrained=True)
            assert args.use_transforms in ["gigapath_default"]

        elif args.model_type == 'phikon':
            model = ViTModel.from_pretrained("owkin/phikon", add_pooling_layer=False)
            assert args.use_transforms in ["uni_default"] ## uni and phikon have same preprocessing

        elif args.model_type == 'hibou_b':
            model = hibou.build_model(args.model_weights_path+"hibou-b.pth")
            assert args.use_transforms in ["hibou_default"]
        
        elif args.model_type == 'hibou_l':
            model = AutoModel.from_pretrained("histai/hibou-l", trust_remote_code=True)
            assert args.use_transforms in ["hibou_default"]

        elif args.model_type == 'kaiko_b8':
            print("kaiko requires python >=3.10")
            model = torch.hub.load("kaiko-ai/towards_large_pathology_fms", "vitb8", trust_repo=True)
            assert args.use_transforms in ["kaiko_default"]
            
        elif args.model_type == 'optimus':
            model = timm.create_model("hf_hub:bioptimus/H-optimus-0", pretrained=True)
            assert args.use_transforms in ["optimus_default"]

        elif args.model_type == 'virchow':
            model = timm.create_model("hf-hub:paige-ai/Virchow", pretrained=True, mlp_layer=timm.layers.SwiGLUPacked, act_layer=torch.nn.SiLU)
            assert args.use_transforms in ["gigapath_default"] ## virchow has same preprocessing as provgigapath
            ## see https://huggingface.co/paige-ai/Virchow/blob/main/config.json

        elif args.model_type == 'virchow2cls':
            model = timm.create_model("hf-hub:paige-ai/Virchow-2", pretrained=True, mlp_layer=timm.layers.SwiGLUPacked, act_layer=torch.nn.SiLU)
            assert args.use_transforms in ["gigapath_default"] ## virchow has same preprocessing as provgigapath

        elif args.model_type == 'gpfm':
            vit_kwargs = dict(
            img_size=224,patch_size=14,init_values=1.0e-05,ffn_layer='mlp',block_chunks=4,qkv_bias=True,proj_bias=True,ffn_bias=True,)
            model = vits.__dict__['vit_large'](**vit_kwargs)
            ckpt = torch.load(os.path.join(args.model_weights_path+"GPFM.pth"))['teacher']       
            
            # rename keys
            new_ckpt = {}
            for k, v in ckpt.items():
                if 'backbone' in k:
                    k = '.'.join(k.split('.')[1:])
                    new_ckpt[k] = v
            msg = model.load_state_dict(new_ckpt)
            print(msg) 
            assert args.use_transforms in ["gpfm_default"]

        elif args.model_type=='HIPT_4K':
            model = HIPT_4K(model256_path=args.model_weights_path+"vit256_small_dino.pth",model4k_path=args.model_weights_path+"vit4k_xs_dino.pth",device256=torch.device('cuda:0'),device4k=torch.device('cuda:0'))
        
        model = model.to(device)
        if torch.cuda.device_count() > 1:
                model = nn.DataParallel(model)
                
        print("\nModel parameters:",f'{sum(p.numel() for p in model.parameters() if p.requires_grad):,}')
        param_size = 0
        for param in model.parameters():
            param_size += param.nelement() * param.element_size()
        buffer_size = 0
        for buffer in model.buffers():
            buffer_size += buffer.nelement() * buffer.element_size()
        size_all_mb = (param_size + buffer_size) / 1024**2
        print('Model size: {:.3f}MB'.format(size_all_mb))
        
        model.eval()
        
        unavailable_patch_files=0
        total_time_elapsed = 0.0
        total = len(bags_dataset)
        times = []
        for bag_candidate_idx in range(total):
            print('\nprogress: {}/{}'.format(bag_candidate_idx, total))
            print('skipped unavailable slides: {}'.format(unavailable_patch_files))
            try:        
                slide_id = str(bags_dataset[bag_candidate_idx]).split(args.slide_ext)[0]
                bag_name = slide_id+'.h5'
                if args.graph_patches == 'big':
                    h5_file_path = os.path.join(args.data_h5_dir,'patches/big',bag_name)
                elif args.graph_patches == 'small':
                    h5_file_path = os.path.join(args.data_h5_dir,'patches/small',bag_name)
                else:
                    h5_file_path = os.path.join(args.data_h5_dir, 'patches', bag_name)
                slide_file_path = os.path.join(args.data_slide_dir, slide_id+args.slide_ext)
                print(slide_id)

                if args.use_transforms == 'all':
                    if not args.no_auto_skip and slide_id+'aug1.pt' in dest_files:
                        print('skipped {}'.format(slide_id))
                        continue
                else:
                    if not args.no_auto_skip and slide_id+'.pt' in dest_files:
                        print('skipped {}'.format(slide_id))
                        continue 

                output_path = os.path.join(args.feat_dir, 'h5_files', bag_name)
                time_start = time.time()
                wsi = openslide.open_slide(slide_file_path)
                output_file_path = compute_w_loader(h5_file_path, output_path, wsi, 
                model = model, batch_size = args.batch_size, verbose = 1, print_every = args.print_every, 
                custom_downsample=args.custom_downsample, target_patch_size=args.target_patch_size)
                time_elapsed = time.time() - time_start
                total_time_elapsed += time_elapsed
                print('\ncomputing features for {} took {} s'.format(output_file_path, time_elapsed))
                times = times + [time_elapsed]
                file = h5py.File(output_file_path, "r")

                features = file['features'][:]
                print('features size: ', features.shape)
                print('coordinates size: ', file['coords'].shape)
                features = torch.from_numpy(features)
                bag_base, _ = os.path.splitext(bag_name)
                torch.save(features, os.path.join(args.feat_dir, 'pt_files', bag_base+'.pt'))
            except KeyboardInterrupt:
                assert 1==2, "keyboard interrupt"
            except:
                print("patch file unavailable")
                unavailable_patch_files = unavailable_patch_files+1 
                continue
        print("finished running with {} unavailable slide patch files".format(unavailable_patch_files))
        print("total time: {}".format(total_time_elapsed))

        if args.save_times:
            os.makedirs("timings", exist_ok=True)
            now = datetime.now()

            f = open("timings/{}-features-{}.txt".format(args.model_type,csv_path[:-4].split("/")[1]), "a")
            f.write("\nrun finished at {}".format(now.strftime("%d/%m/%Y, %H:%M:%S")))
            f.write("\nfinished running with {} unavailable slide patch files".format(unavailable_patch_files))
            f.write("\ntotal time: {}".format(total_time_elapsed))
            f.write("\ntimes: {}".format(times))
            f.close()
