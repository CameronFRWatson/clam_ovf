import torch
import torch.nn as nn
from math import floor
import os
import random
import numpy as np
import pdb
import time
from datasets.dataset_h5 import Dataset_All_Bags, Whole_Slide_Bag_FP
import torch
from torchvision import transforms
from torch.utils.data import DataLoader
from models.resnet_custom import resnet50_baseline
import argparse
from utils.utils import print_network, collate_features
from utils.file_utils import save_hdf5
from PIL import Image
import h5py
import openslide
import timm
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
        
        if args.use_transforms=='all':
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
        
        else:

            dataset = Whole_Slide_Bag_FP(file_path=file_path, wsi=wsi, pretrained=pretrained, 
                custom_downsample=custom_downsample, target_patch_size=target_patch_size)
        dataset.update_sample(range(len(dataset)))
        x, y = dataset[0]
        if args.model_type=='resnet50':
            kwargs = {'num_workers': 4, 'pin_memory': True} if device.type == "cuda" else {}
        elif args.model_type=='levit_128s':
            kwargs = {'num_workers': 16, 'pin_memory': True} if device.type == "cuda" else {}
            tfms=torch.nn.Sequential(transforms.CenterCrop(224))
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
parser.add_argument('--custom_downsample', type=int, default=1)
parser.add_argument('--target_patch_size', type=int, default=-1)
parser.add_argument('--pretraining_dataset',type=str,choices=['ImageNet','Histo'],default='ImageNet')
parser.add_argument('--model_type',type=str,choices=['resnet50','levit_128s'],default='resnet50')
parser.add_argument('--use_transforms',type=str,choices=['all','spatial','none'],default='none')
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

        print('loading {} pretrained model'.format(args.pretraining_dataset))
        if args.model_type=='resnet50':
            model = resnet50_baseline(pretrained=True,dataset=args.pretraining_dataset)
        elif args.model_type=='levit_128s':
            model=timm.create_model('levit_256',pretrained=True, num_classes=0)    
        model = model.to(device)
        
        if torch.cuda.device_count() > 1:
                model = nn.DataParallel(model)
                
        model.eval()
        total = len(bags_dataset)

        for bag_candidate_idx in range(total):
                slide_id = bags_dataset[bag_candidate_idx].split(args.slide_ext)[0]
                bag_name = slide_id+'.h5'
                h5_file_path = os.path.join(args.data_h5_dir, 'patches', bag_name)
                slide_file_path = os.path.join(args.data_slide_dir, slide_id+args.slide_ext)
                print('\nprogress: {}/{}'.format(bag_candidate_idx, total))
                print(slide_id)

                if not args.no_auto_skip and slide_id+'.pt' in dest_files:
                        print('skipped {}'.format(slide_id))
                        continue 

                output_path = os.path.join(args.feat_dir, 'h5_files', bag_name)
                time_start = time.time()
                wsi = openslide.open_slide(slide_file_path)
                output_file_path = compute_w_loader(h5_file_path, output_path, wsi, 
                model = model, batch_size = args.batch_size, verbose = 1, print_every = 20, 
                custom_downsample=args.custom_downsample, target_patch_size=args.target_patch_size)
                time_elapsed = time.time() - time_start
                print('\ncomputing features for {} took {} s'.format(output_file_path, time_elapsed))
                file = h5py.File(output_file_path, "r")

                features = file['features'][:]
                print('features size: ', features.shape)
                print('coordinates size: ', file['coords'].shape)
                features = torch.from_numpy(features)
                bag_base, _ = os.path.splitext(bag_name)
                torch.save(features, os.path.join(args.feat_dir, 'pt_files', bag_base+'.pt'))



