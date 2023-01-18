import argparse
import pandas as pd
from sklearn.metrics import confusion_matrix, f1_score, accuracy_score,balanced_accuracy_score, roc_auc_score
import numpy as np

parser = argparse.ArgumentParser(description='Model names input split by commas')
parser.add_argument('--model_names', type=str, default=None,help='models to plot')
parser.add_argument('--bootstraps', type=int, default=100000,
                    help='Number of bootstraps to calculate')
parser.add_argument('--run_repeats', type=int, default=10,
                            help='Number of model repeats')
parser.add_argument('--folds', type=int, default=10,
                            help='Number of cross-validation folds')
parser.add_argument('--data_csv', type=str, default='set_all_714.csv')
parser.add_argument('--num_classes',type=int,default=2)
args = parser.parse_args()
model_names=args.model_names.split(",")
bootstraps=args.bootstraps

for model_name in model_names:
    full_model_name='eval_results/EVAL_'+model_name

    all_auc_means=[]
    all_f1_means=[]
    all_accuracy_means=[]
    all_balanced_accuracy_means=[]
    all_auc_sds=[]
    all_f1_sds=[]
    all_accuracy_sds=[]
    all_balanced_accuracy_sds=[]

    for run_no in range(args.run_repeats):
            
        all_Yhats=[]
        all_Ys=[]
        all_p1s=[]
        all_probs=[]

        print("run: ",run_no)
        for fold_no in range(args.folds):
            if args.run_repeats>1:
                full_df = pd.read_csv(full_model_name+'_run{}/fold_{}.csv'.format(run_no,fold_no))
            else:
                full_df = pd.read_csv(full_model_name+'/fold_{}.csv'.format(fold_no))
            all_Yhats=all_Yhats+list(full_df['Y_hat'])
            all_Ys=all_Ys+list(full_df['Y'])
            if args.num_classes==2:
                all_p1s=all_p1s+list(full_df['p_1'])
            else:
                if len(all_probs)<1:
                    all_probs=full_df.iloc[:,-args.num_classes:]
                else:
                    all_probs=all_probs.append(full_df.iloc[:,-args.num_classes:])

        AUC_scores=[]
        err_scores=[]
        accuracies=[]
        f1s=[]
        balanced_accuracies=[]
    
        for _ in range(bootstraps):
            idxs=np.random.choice(range(len(all_Ys)),len(all_Ys))
            if args.num_classes==2:
                f1s=f1s+[f1_score([all_Ys[idx] for idx in idxs],[all_Yhats[idx] for idx in idxs])]
                AUC_scores=AUC_scores+[roc_auc_score([all_Ys[idx] for idx in idxs],[all_p1s[idx] for idx in idxs])]
            else:
                f1s=f1s+[f1_score([all_Ys[idx] for idx in idxs],[all_Yhats[idx] for idx in idxs],average='macro')]
                AUC_scores=AUC_scores+[roc_auc_score([all_Ys[idx] for idx in idxs],[all_probs.iloc[idx,:] for idx in idxs],multi_class='ovr')]
            accuracies=accuracies+[accuracy_score([all_Ys[idx] for idx in idxs],[all_Yhats[idx] for idx in idxs])]
            balanced_accuracies=balanced_accuracies+[balanced_accuracy_score([all_Ys[idx] for idx in idxs],[all_Yhats[idx] for idx in idxs])]
            
        all_auc_means=all_auc_means+[np.mean(AUC_scores)]
        all_auc_sds=all_auc_sds+[np.std(AUC_scores)]
        all_f1_means=all_f1_means+[np.mean(f1s)]
        all_f1_sds=all_f1_sds+[np.std(f1s)]
        all_accuracy_means=all_accuracy_means+[np.mean(accuracies)]
        all_accuracy_sds=all_accuracy_sds+[np.std(accuracies)]
        all_balanced_accuracy_means=all_balanced_accuracy_means+[np.mean(balanced_accuracies)]
        all_balanced_accuracy_sds=all_balanced_accuracy_sds+[np.std(balanced_accuracies)]

        print("AUC mean: ", all_auc_means," AUC std: ",all_auc_sds)
        if args.num_classes==2:
            print("F1 mean: ",all_f1_means," F1 std: ",all_f1_sds)
        else:
            print("Marco F1 mean: ",all_f1_means," F1 std: ",all_f1_sds)
        print("accuracy mean: ",all_accuracy_means," accuracy std: ",all_accuracy_sds)
        print("balanced accuracy mean: ",all_balanced_accuracy_means," balanced accuracy std: ",all_balanced_accuracy_sds)
    df=pd.DataFrame([[all_auc_means],[all_accuracy_means],[all_balanced_accuracy_means],[all_f1_means],[all_auc_sds],[all_accuracy_sds],[all_balanced_accuracy_sds],[all_f1_sds]])
    df.to_csv("metric_results/"+model_name+".csv",index=False)

