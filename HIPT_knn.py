#from ..HIPT.Weakly-Supervised-Subtyping.models.model_hierarchical_mil import HIPT_LGP_FC
import torch
import torch.nn.functional as F
import os
import pandas as pd
import random
import sys
sys.path.append('../HIPT/Weakly-Supervised-Subtyping/')
sys.path.append('../HIPT/1-Hierarchical-Pretraining/')
sys.path.append('../HIPT/HIPT_4K/')
from eval_knn import knn_classifier
from models.model_hierarchical_mil import HIPT_LGP_FC
from sklearn.neighbors import KNeighborsClassifier
from sklearn.model_selection import cross_val_score, StratifiedKFold
import numpy as np

self = HIPT_LGP_FC()

#df = pd.read_csv('dataset_csv/set_treatment.csv',header=0)
df = pd.read_csv('dataset_csv/ESGO_train_staging.csv',header=0)
#df = pd.read_csv('dataset_csv/ESGO_train_all.csv',header=0)

def agg_slide_feature(region_features):
    h_4096 = self.global_phi(region_features)
    h_4096 = self.global_transformer(h_4096.unsqueeze(1)).squeeze(1)
    A_4096, h_4096 = self.global_attn_pool(h_4096)
    A_4096 = torch.transpose(A_4096, 1, 0)
    A_4096 = F.softmax(A_4096, dim=1)
    h_path = torch.mm(A_4096, h_4096)
    h_WSI = self.global_rho(h_path)
    return h_WSI

data_root_dir = "../mount_outputs/features"
#features_folder = "treatment_Q90_hipt4096_features_normalised_updatedsegmentation"
features_folder = "ovarian_leeds_hipt4096_features_normalised"
data_dir = os.path.join(data_root_dir, features_folder)

x=None
labels=[]
for row in df.iterrows():
    slide_id = row[1]['slide_id']
    labels = labels +  [row[1]['label']]
    full_path = os.path.join(data_dir, 'pt_files', '{}.pt'.format(slide_id))
    h_4096 = torch.load(full_path)
    h_WSI = agg_slide_feature(h_4096)
    if x is None:
        x = torch.unsqueeze(h_WSI, dim=0)
    else:
        x = torch.cat((x,torch.unsqueeze(h_WSI, dim=0)),0)

for i in range(len(labels)):
    if labels[i]=='high_grade':
        labels[i]=1
    else:
        labels[i]=0

assert sum(labels) < len(labels)

train_ids = random.sample(range(len(labels)),181)
test_ids = list(set(range(len(labels)))-set(train_ids))

train_labels = [labels[idx] for idx in train_ids]
test_labels = [labels[idx] for idx in test_ids]

#print("max train_ids",max(train_ids))
#print("shape x",x.shape)
train_x = torch.index_select(x, 0, torch.tensor(train_ids)).squeeze(1)
test_x = torch.index_select(x, 0, torch.tensor(test_ids)).squeeze(1)

k = 5

## trying testing on training data to check its doing something reasonable
#test_ids = train_ids
#test_labels = train_labels
#test_x = train_x
#k = 3

#print(train_x.shape)
#print(train_labels)

print("starting knn")

## voting temperature T turned out very important, the default 0.07 led to terrible results even when using train set for testing
#top1 = knn_classifier(train_x,torch.tensor(train_labels),test_x,torch.tensor(test_labels),k,T=1,num_classes=2)

#print("{} nearest neighbor results with random train/test split".format(k))
#print("accuracy:", top1)



## trying the code from https://github.com/mahmoodlab/HIPT/blob/master/3-Self-Supervised-Eval/slide_extraction-evaluation.ipynb instead
embeddings_all = x.detach().squeeze(1)#torch.stack(x).numpy()
labels_all = np.array(labels)             
                              
clf = KNeighborsClassifier()
skf = StratifiedKFold(n_splits=10, shuffle=True, random_state=0)
label_dict = {'high_grade':0,'low_grade':1,'clear_cell':1,'endometrioid':1,'mucinous':1}

if len(label_dict.keys()) > 2:
    scores = cross_val_score(clf, embeddings_all, labels_all, cv=skf, scoring='roc_auc_ovr')
else:
    scores = cross_val_score(clf, embeddings_all, labels_all, cv=skf, scoring='roc_auc')
print("all scores:",scores)
print("mean score:",scores.mean())
