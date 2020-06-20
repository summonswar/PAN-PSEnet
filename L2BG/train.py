import torch
from model import GradCL, Flatten, get_alexnet_template#, visual_context MLT
from modules.sequence_modeling import BidirectionalLSTM
import pmnist_dataset
from datasetv1 import AlignCollate
from mlt_loader import get_MLT_loaders
import torch
import torch.nn as nn
import torch.optim as optim
import torch.nn.init as init
import copy
from RSA import RSA,pyRSA
from collections import OrderedDict, deque
from torch.utils.data import Subset
from pmnist_dataset import get_Pmnist_tasks
from dataloaders.datasetGen import SplitGen
from dataloaders.base import get_tasks
from VDD_loader import get_tasks_VDD
#from test import validation_CL
from nltk.metrics.distance import edit_distance
import torch.backends.cudnn as cudnn
import dataloaders.base
import matplotlib.pyplot as plt
import L2G_config as M
import logging
import argparse
import time
import random
import math
import os
import numpy as np
import sys

from plot_jnet import viz_jnet


random.seed(1111)
torch.manual_seed(1111)
np.random.seed(1111)
device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
print(device)


def get_layer_names(p1):
    layer_set = set()
    for key in p1.keys():
        key = key.split('.')[0]
        layer_set.add(key)

    return list(layer_set)


def compute_num_para(model):
    num_paras = 0 
    for name,para in model.named_parameters():
        num_paras += np.prod(np.array(para.size()))
    return num_paras


def weight_innit(model):
    # weight initialization
    for name, param in model.named_parameters():
        if 'localization_fc2' in name:
            print(f'Skip {name} as it is already initialized')
            continue
        try:
            if 'bias' in name:
                init.constant_(param, 0.0)
            elif 'weight' in name:
                init.kaiming_normal_(param)
                #init.constant_(param, 0.0)
        except Exception as e:  # for batchnorm.
            if 'weight' in name:
                param.data.fill_(1)
            continue
    return model


def validation_CL(model,criterion,evaluation_loader,task):
    n_correct = 0
    norm_ED = 0
    length_of_data = 0
    infer_time = 0
    valid_loss_avg = Averager()
    if task in ['hin','arab','ban']:
        converter = evaluation_loader.dataset.dataset.datasets[0].labelconverter
    else:
        converter = evaluation_loader.dataset.datasets[0].labelconverter

    for i, (image_tensors, labels) in enumerate(evaluation_loader):
        batch_size = image_tensors.size(0)
        length_of_data = length_of_data + batch_size
        image = image_tensors.to(device)

        text_for_loss, length_for_loss = converter.encode(labels, batch_max_length=30)

        preds = model(image, task).log_softmax(2)

        preds_size = torch.IntTensor([preds.size(1)] * batch_size)
        preds = preds.permute(1, 0, 2)  # to use CTCloss format
        torch.backends.cudnn.enabled = False
        cost = criterion(preds, text_for_loss.cuda(), preds_size, length_for_loss)

        # Select max probabilty (greedy decoding) then decode index to character
        _, preds_index = preds.max(2)
        preds_index = preds_index.transpose(1, 0).contiguous().view(-1)
        preds_str = converter.decode(preds_index.data, preds_size.data)
        valid_loss_avg.add(cost)
        # calculate accuracy.
    
        for pred, gt in zip(preds_str, labels):
            #print('pred:',pred)
            #print('gt:',gt)
            if pred == gt:
                n_correct += 1
            if len(gt) == 0:
                norm_ED += 1
                print('NULL')
            else:
                #print('predicted:',pred,'gt:',gt)
                #print(edit_distance(pred, gt) / max(len(gt),len(pred)))
                norm_ED += 1-(edit_distance(pred, gt) / max(len(gt),len(pred)))
                #print(norm_ED)
        #print(norm_ED)
    #cnt = cnt+1

    norm_ED = norm_ED/float(length_of_data)
    #norm_ED = 1-norm_ED
    accuracy = n_correct / float(length_of_data) * 100

    print('Test set: Avg. loss: {:.4f}, Accuracy: {}/{} ({:.0f}%) Norm ED: {:.2f}'.format(
            valid_loss_avg.val(), n_correct, length_of_data,accuracy,norm_ED))

    return norm_ED, valid_loss_avg.val()

def test(model,criterion,test_loader,task,datamode):
    device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
    print('testing task:',task)
    model = model.eval()
    test_loss = 0
    correct = 0
    with torch.no_grad():
        for data in test_loader:
            x, y = data_trasnsform(data,datamode)
            x = x.to(device)
            y = y.to(device)
            output = model(x,task)
            test_loss += criterion(output, y).item()
            pred = output.data.max(1, keepdim=True)[1]
            correct += pred.eq(y.data.view_as(pred)).sum()
        test_loss /= len(test_loader.dataset)
    print('Test set: Avg. loss: {:.4f}, Accuracy: {}/{} ({:.0f}%)'.format(
            test_loss, correct, len(test_loader.dataset),
            100. * correct / len(test_loader.dataset)))
    model = model.train()
    return float(((100.*correct)/len(test_loader.dataset))),test_loss



def train_single_task(model,criterion,optimizer,trainloader,valloader,new_task,datamode,epochs=3):
    start_time = time.time()
    print_step = math.ceil(len(trainloader)/10)
    if datamode == 'mltr' and new_task in ['hin','arab','ban']:
        converter = trainloader.dataset.dataset.datasets[0].labelconverter
    elif datamode == 'mltr':
        converter = trainloader.dataset.datasets[0].labelconverter

    for epoch in range(epochs):
        for batch_idx, data in enumerate(trainloader):
            if datamode == 'mltr':
                x,labels = data_trasnsform(data,datamode)
                x = x.to(device)
                text, length = converter.encode(labels, batch_max_length=30)
                batch_size = x.size(0) 
                output = model(x,new_task).log_softmax(2)
                output_size = torch.IntTensor([output.size(1)] * batch_size)
                output = output.permute(1, 0, 2)  # to use CTCLoss format
                torch.backends.cudnn.enabled = False
                loss = criterion(output, text.to(device), output_size.to(device), length.to(device))
                torch.backends.cudnn.enabled = True
            else:
                x, y = data_trasnsform(data,datamode)
                x = x.to(device)
                y = y.to(device)
                output = model(x,new_task)
                loss = criterion(output,y)
            model.zero_grad()
            loss.backward()
            optimizer.step()
            if batch_idx % print_step == 0:
                print('Train Epoch: {} [{}/{} ({:.0f}%)]\tLoss: {:.6f}'.format(
                epoch, batch_idx * len(x), len(trainloader.dataset),
                100. * batch_idx / len(trainloader), loss.item()))
        if datamode == 'mltr':
            acc,_ = validation_CL(model,criterion,valloader,new_task)
        else:
            acc, _ = test(model,criterion,valloader,new_task,datamode)
    end_time = time.time()
    train_time = round(end_time-start_time,2)
    print("training time:",train_time)
    logging.debug("training time: %f",train_time)
    logging.debug('Final Accuracy %f',acc)
    return acc


def score_to_distribution(layer_scores):
    norm_const = sum(list(layer_scores.values()))
    for key in layer_scores.keys():
        layer_scores[key] = round(layer_scores[key]/norm_const,3)
    return layer_scores


def freeze_past(model,new_task,method='freeze'):
    if method == 'freeze':
        for name, para in model.named_parameters(): 
            if new_task in name:
                para.requires_grad = True
            else:
                para.requires_grad = False
        return []
    if method == 'slow_lr':
        new_paras = []
        shared_paras = []
        for name, para in model.named_parameters(): 
            if new_task in name:
                new_paras.append(para)
            else:
                shared_paras.append(para)

        return new_paras, shared_paras


def freeze_past_sanity_check(model):
    for name,para in model.named_parameters():
        print(name,para.requires_grad)


def find_similar_tasks(task_sim_scores,n=3):
    
    result = {k: v for k, v in sorted(task_sim_scores.items(), key=lambda item: item[1])}
    selected = list(result.keys())
    selected = selected[0:n]
    return selected


def get_500_images_loader(train_loader,datamode):
    total_data_points = len(train_loader.dataset)
    indices = random.sample(range(0,total_data_points),500)
    dataset_500 = Subset(train_loader.dataset,indices)
    
    if datamode == 'mltr':
        AlignCollate_valid = AlignCollate(imgH=32, imgW=100, keep_ratio_with_pad=False)
        return torch.utils.data.DataLoader(dataset_500, batch_size=20, shuffle=False, num_workers=1,collate_fn=AlignCollate_valid)
    else:
        return torch.utils.data.DataLoader(dataset_500, batch_size=20, shuffle=False, num_workers=1)


def flatten_filter_activations(activations,datamode):
    if datamode == 'pmnist' or datamode == 'smnist':
        filter_activations = {}
        for name, act in activations.items():
            if not 'task_heads' in name:
                filter_activations[name] = act.view(act.size(0),-1)
        return filter_activations
    else:
        filter_activations = {}
        for name, act in activations.items():
            if not 'task_heads' in name:
                filter_activations[name] = act.view(act.size(0),-1)
        return filter_activations


def data_trasnsform(data,datamode):
    if datamode == 'mltr':
        x = data[0]
        labels = data[1]
        return x,labels
    if datamode == 'pmnist' or datamode == 'smnist':
        x = data[0]
        x = x.view(x.size(0),32*32*1)
        y = data[1]
        return x,y
    else:
        x = data[0]
        y = data[1]
        return x, y


def get_activations(model,image_loader_500,task,datamode):
    accumulate_activations = {}
    with torch.no_grad():
        for idx, data in enumerate(image_loader_500):
            x, _ = data_trasnsform(data,datamode)
            x = x.to('cuda')
            activations = model.forward(x,task,True)
            activations = flatten_filter_activations(activations,datamode)
            for name,layer_activations in activations.items():
                if idx == 0:
                    accumulate_activations[name] = layer_activations
                else:
                    accumulate_activations[name] = torch.cat((accumulate_activations[name],layer_activations),0)
    return accumulate_activations


def RSA_Sim(model,new_task,tasks,train_loaders,val_loaders,activations_new_task,image_loader_500,datamode):
    start_time = time.time()
    pairwise_layer_scores = {}
    pairwise_task_scores = {}
    for task in tasks:
        activations_task = get_activations(model,image_loader_500,task,datamode)
        pairwise_layer_scores[task] = {}
        for name in activations_task:
            #rsa = pyRSA(activations_new_task[name],activations_task[name])
            rsa = RSA(activations_new_task[name],activations_task[name])
            rsa.create_RDMs()
            rsa.compute_RDM_similarity()
            pairwise_layer_scores[task][name] = round(rsa.similarity,3)
        pairwise_task_scores[task] = round(np.linalg.norm(list(pairwise_layer_scores[task].values()))/2,3)
        print(task,':',pairwise_layer_scores[task])
        pairwise_layer_scores[task] = score_to_distribution(pairwise_layer_scores[task])

    end_time = time.time()
    function_time = end_time - start_time
    print('time taken for RSA_Sim execution', function_time)
    return pairwise_layer_scores, pairwise_task_scores


def get_new_task_activations(image_loader_500,new_task,train_loaders,val_loaders,datamode,epochs,path_pretrain,lr,n_class=10):
    if datamode == 'pmnist' or datamode == 'smnist' :
        template = {'linear1_input':nn.Linear(32*32,300),'relu1':nn.ReLU(),
                    'linear2':nn.Linear(300,300),'relu2':nn.ReLU(),
                    'linear3':nn.Linear(300,300),'relu3':nn.ReLU()}

    if datamode == 'CIFAR100':
        template = {'conv2d_input1':nn.Conv2d(3,64,4),'maxpool1':nn.MaxPool2d(2),'relu1':nn.ReLU(),
                    'conv2d_2':nn.Conv2d(64,128,3),'maxpool2':nn.MaxPool2d(2),'relu2':nn.ReLU(),
                    'conv2d_3':nn.Conv2d(128,256,2),'maxpool3':nn.MaxPool2d(2),'relu3':nn.ReLU(),
                    'adaptavgpool2d':Flatten(),'linear1':nn.Linear(1024,2048),
                    'linear2':nn.Linear(2048,2048)}

    if datamode == 'VDD':
        template = get_alexnet_template(pretrained=True)

    if datamode == 'mltr':
        template = {'conv2d_input1':nn.Conv2d(1, 64, 3, 1, 1), 'relu1':nn.ReLU(),'maxpool1':nn.MaxPool2d(2, 2),
            'conv2d_2':nn.Conv2d(64, 128, 3, 1, 1),'relu2':nn.ReLU(),'maxpool2':nn.MaxPool2d(2, 2),
            'conv2d_3':nn.Conv2d(128, 256, 3, 1, 1), 'relu3':nn.ReLU(),
            'conv2d_4':nn.Conv2d(256, 256, 3, 1, 1), 'relu4':nn.ReLU(True),'maxpool3':nn.MaxPool2d((2, 1), (2, 1)),
            'conv2d_5':nn.Conv2d(256, 512, 3, 1, 1, bias=False),'bn1':nn.BatchNorm2d(512), 'relu5':nn.ReLU(),
            'conv2d_6':nn.Conv2d(512, 512, 3, 1, 1, bias=False),'bn2':nn.BatchNorm2d(512), 'relu6':nn.ReLU(),'maxpool4':nn.MaxPool2d((2, 1), (2, 1)),
            'conv2d_7':nn.Conv2d(512, 512, 2, 1, 0), 'relu7':nn.ReLU(True),'vis_context':visual_context(),
            'BiLstm_1':BidirectionalLSTM(512,256,256),
            'BiLstm_2':BidirectionalLSTM(256,256,256)}
        
    model_temp = GradCL(template,0.5)
    model_temp.to('cuda')
    model_temp.init_subgraph(new_task,datamode,n_class)
    model_temp.to('cuda')
    #optimizer_temp = optim.SGD(model_temp.parameters(), lr=lr, momentum=0.9)
    if datamode == 'mltr':
        criterion = torch.nn.CTCLoss(zero_infinity=True)
        optimizer_temp = optim.Adadelta(model_temp.parameters(), lr=lr, rho=0.95, eps=1e-8)
    else:
        criterion = nn.CrossEntropyLoss()
        optimizer_temp = optim.SGD(model_temp.parameters(), lr=lr, momentum=0.9)
    logging.debug('---->Growth step1: Training new task %s for RDM creation',new_task)
    print('---->Growth step1: Training new task ',new_task,' for RDM creation')
    model_name = datamode+'_'+new_task+'_'+str(epochs)+'.pth'
    if not os.path.exists(path_pretrain+model_name):
        acc = train_single_task(model_temp,criterion,optimizer_temp,train_loaders[new_task],val_loaders[new_task],new_task,datamode,epochs)
        print(path_pretrain)
        model_temp.save_model(path_pretrain+model_name)
    else:
        print('Loading pretrained model for getting activations')
        print('Loaded model:',model_name)
        model_temp.load_model(path_pretrain+model_name)
        model_temp.to('cuda')
        acc,_ = test(model_temp,criterion,val_loaders[new_task],new_task,datamode)
    activations_new_task = get_activations(model_temp,image_loader_500,new_task,datamode)
    return activations_new_task, acc


def learn_to_grow(model,criterion,train_loaders,val_loaders,task_names,datamode,pretrain_path,lr,freeze=None,task_classes=None,epochs=10,similarity='pearson'):
    birth = time.time()
    print('###Initiating Growing Process###')
    logging.debug('###Initiating Growing Process###')
    tasks = []
    acc_mono = []
    avg_acc_list = []
    avg_diff_list = []
    task_counter = []
    start_time = 0
    end_time = 0
    temp_string = ''
    for task_id, new_task in enumerate(task_names):
        print("\n----->Begin Growth sequence of",new_task)
        logging.debug("\n----->Begin Growth sequence of %s",new_task)
        if datamode == 'pmnist':
            n_classes = task_classes
            if task_id == 0:
                model.init_subgraph(new_task,datamode,n_classes)
            else:
                model.init_subgraph(new_task,datamode)
        if datamode == 'CIFAR100':
            n_classes = task_classes
            model.init_subgraph(new_task,datamode,n_classes)
        if datamode == 'VDD' or datamode == 'mltr':
            n_classes = task_classes[new_task]
            model.init_subgraph(new_task,datamode,n_classes)


        if task_id == 0:
            print('Only Training for initial task')
            logging.debug('Only Training for initial task')
            #optimizer = optim.SGD(model.parameters(), lr=0.01, momentum=0.9)
            if datamode == 'mltr':
                optimizer = optim.Adadelta(model.parameters(), lr=lr, rho=0.95, eps=1e-8)
            else:
                optimizer = optim.SGD(model.parameters(), lr=lr, momentum=0.9)
            model = weight_innit(model)
            model.to(device)
            acc = train_single_task(model,criterion,optimizer,train_loaders[new_task],val_loaders[new_task],new_task,datamode,epochs)
            tasks.append(new_task)
            avg_acc_list.append(acc)
            avg_diff_list.append(0)
            task_counter.append(1)
            acc_mono.append(acc)
            continue
        task_sim_scores = {}
        task_layerwise_sims = {}
        
        if similarity == 'RSA':
            image_loader_500 = get_500_images_loader(train_loaders[new_task],datamode)
            activations_new_task, acc = get_new_task_activations(image_loader_500,new_task,train_loaders,val_loaders,datamode,epochs,pretrain_path,lr,n_classes)
            task_layerwise_sims, task_sim_scores = RSA_Sim(model,new_task,tasks,train_loaders,val_loaders,activations_new_task,image_loader_500,datamode)
            acc_mono.append(acc)

        if similarity == 'random_growth':
            selected_tasks = list(np.random.choice(tasks,1))
        else:
            selected_tasks = find_similar_tasks(task_sim_scores,n=1)

        print('\n---->Growth step2: Find most similar task')
        logging.debug('\n---->Growth step2: Find most similar task')
        #returns list containing k similar tasks
        print(task_sim_scores)
        logging.debug('%s',task_sim_scores)
        logging.debug('Selected Task %s',selected_tasks[0])
        print('Selected Task',selected_tasks[0])

        logging.debug('\n---->Growth step3: Model Growth')
        print('\n---->Growth step3: Model Growth')
        if similarity == 'random_growth':
            acc = train_single_task(model,criterion,optimizer,train_loaders[new_task],val_loaders[new_task],new_task,datamode,epochs)
            acc_mono.append(acc)
            model.random_growth(new_task,selected_tasks)
        else:
            model.grow_graph(new_task,selected_tasks,task_layerwise_sims)
        #print("After Growing")
        #freeze_past_sanity_check(model)
        #account for new parameters added
        if freeze=='slow_lr':
            new_paras, shared_paras = freeze_past(model,new_task,freeze)
            optimizer = optim.SGD([
                {'params': new_paras},
                {'params': shared_paras, 'lr': lr/10}
            ], lr=lr, momentum=0.9)
        if freeze == 'freeze':
            _ = freeze_past(model,new_task,freeze)
            #print("After freezing")
            #freeze_past_sanity_check(model)
            optimizer = optim.SGD(model.parameters(), lr=lr, momentum=0.9)
        if freeze == 'none':
            optimizer = optim.SGD(model.parameters(), lr=lr, momentum=0.9)
        
        #print("After optimizer call")
        #freeze_past_sanity_check(model)
        print('\n---->Growth step4: Training grown model on new_task', new_task)
        logging.debug('\n---->Growth step4: Training grown model on new_task %s', new_task)
        model.to(device)
        train_single_task(model,criterion,optimizer,train_loaders[new_task],val_loaders[new_task],new_task,datamode,epochs)
        tasks.append(new_task)
        print('\n######Testing on previous tasks after training task:',new_task,'######')
        logging.debug('\n######Testing on previous tasks after training task: %s ######',new_task)
        avg_accuracy = 0
        avg_diff = 0
        for idx,task in enumerate(tasks):
            if datamode == 'mltr':
                temp_acc,_ = validation_CL(model,criterion,val_loaders[task],task)
                logging.debug('%s accuracy: %f',task,temp_acc)
            else:
                temp_acc,_ = test(model,criterion,val_loaders[task],task,datamode)
                logging.debug('%s accuracy: %f',task,temp_acc)
            temp_diff = acc_mono[idx]
            avg_diff += temp_acc-temp_diff
            avg_accuracy += temp_acc
        print('average Accuracy:',avg_accuracy/len(tasks))
        print('average diff in accuracy:',avg_diff/len(tasks))
        logging.debug('average Accuracy: %f',avg_accuracy/len(tasks))
        logging.debug('average diff in accuracy: %f',avg_diff/len(tasks))
        logging.debug('######################################################################')
        print('######################################################################')
        task_counter.append(len(tasks))
        avg_acc_list.append(avg_accuracy/len(tasks))
        avg_diff_list.append(avg_diff/len(tasks))
    print('###End of training###')
    death = time.time()
    print('Total Time:',death-birth)
    for name,_ in model.named_parameters():
        print(name)
    return task_counter, avg_acc_list, avg_diff_list


def run_learn_to_grow(opt):
    epochs = opt.epochs
    batch_size = opt.batch_size
    datamode = opt.datamode
    freeze_past = opt.freeze_past
    lr = opt.lr
    logging.basicConfig(filename= opt.exp_dir+'/'+opt.exp_name+'.log', level=logging.DEBUG)
    if opt.datamode == 'pmnist':
        train_loaders, val_loaders = get_Pmnist_tasks(opt.n_tasks,batch_size)
        task_names = list(train_loaders.keys())
        template = {'linear1_input':nn.Linear(32*32,300),'relu1':nn.ReLU(),
                    'linear2':nn.Linear(300,300),'relu2':nn.ReLU(),
                    'linear3':nn.Linear(300,300),'relu3':nn.ReLU()}
        task_classes = 10
    if opt.datamode == 'smnist':
        train_dataset, val_dataset = dataloaders.base.__dict__['MNIST']('datasets/')
        train_dataset_splits, val_dataset_splits, task_output_space = SplitGen(train_dataset, val_dataset,
                                                                          first_split_sz=2,
                                                                          other_split_sz=2,
                                                                          rand_split=False,
                                                                          remap_class=True)
        train_loaders, val_loaders = get_tasks(train_dataset_splits, val_dataset_splits,batch_size)
        task_names = list(train_loaders.keys())
        template = {'linear1_input':nn.Linear(32*32,300),'relu1':nn.ReLU(),
                'linear2':nn.Linear(300,300),'relu2':nn.ReLU(),
                'linear3':nn.Linear(300,300),'relu3':nn.ReLU()}
        task_classes = 2
    if opt.datamode == 'CIFAR100':
        train_dataset, val_dataset = dataloaders.base.__dict__['CIFAR100']('datasets/')
        train_dataset_splits, val_dataset_splits, task_output_space = SplitGen(train_dataset, val_dataset,
                                                                          first_split_sz=10,
                                                                          other_split_sz=10,
                                                                          rand_split=False,
                                                                          remap_class=True)
        train_loaders, val_loaders = get_tasks(train_dataset_splits, val_dataset_splits,batch_size)
        task_names = list(train_loaders.keys())
        template = {'conv2d_input1':nn.Conv2d(3,64,4),'maxpool1':nn.MaxPool2d(2),'relu1':nn.ReLU(),
              'conv2d_2':nn.Conv2d(64,128,3),'maxpool2':nn.MaxPool2d(2),'relu2':nn.ReLU(),
              'conv2d_3':nn.Conv2d(128,256,2),'maxpool3':nn.MaxPool2d(2),'relu3':nn.ReLU(),
              'adaptavgpool2d':Flatten(),'linear1':nn.Linear(1024,2048),'relu4':nn.ReLU(),
              'linear2':nn.Linear(2048,2048),'relu5':nn.ReLU()}
        task_classes = 10
    if opt.datamode == 'VDD':
        task_names = ['cifar100','svhn','ucf101','gtsrb','daimlerpedcls','vgg-flowers','aircraft','dtd']
        train_loaders, val_loaders, task_classes = get_tasks_VDD(task_names,opt.data_dir,opt.imdb_dir,batch_size,opt.data_usage)
        template = get_alexnet_template(pretrained=True)
    if opt.datamode == 'mltr':
        task_names = ['hin','arab','ban','kan','tel']
        train_loaders, val_loaders, task_classes = get_MLT_loaders(task_names,batch_size)
        template = {'conv2d_input1':nn.Conv2d(1, 64, 3, 1, 1), 'relu1':nn.ReLU(),'maxpool1':nn.MaxPool2d(2, 2),
            'conv2d_2':nn.Conv2d(64, 128, 3, 1, 1),'relu2':nn.ReLU(),'maxpool2':nn.MaxPool2d(2, 2),
            'conv2d_3':nn.Conv2d(128, 256, 3, 1, 1), 'relu3':nn.ReLU(),
            'conv2d_4':nn.Conv2d(256, 256, 3, 1, 1), 'relu4':nn.ReLU(True),'maxpool3':nn.MaxPool2d((2, 1), (2, 1)),
            'conv2d_5':nn.Conv2d(256, 512, 3, 1, 1, bias=False),'bn1':nn.BatchNorm2d(512), 'relu5':nn.ReLU(),
            'conv2d_6':nn.Conv2d(512, 512, 3, 1, 1, bias=False),'bn2':nn.BatchNorm2d(512), 'relu6':nn.ReLU(),'maxpool4':nn.MaxPool2d((2, 1), (2, 1)),
            'conv2d_7':nn.Conv2d(512, 512, 2, 1, 0), 'relu7':nn.ReLU(True),'vis_context':visual_context(),
            'BiLstm_1':BidirectionalLSTM(512,256,256),
            'BiLstm_2':BidirectionalLSTM(256,256,256)}

    if opt.run_single:
        index = task_names.index(opt.single_task_name)
        task_names_sub = [task_names[index]]
    else:
        task_names_sub = task_names[0:opt.n_tasks]
    model = GradCL(template,opt.grc)
    model.to(device)
    print(model)
    if datamode == 'mltr':
        criterion = torch.nn.CTCLoss(zero_infinity=True)
    else:
        criterion = nn.CrossEntropyLoss()
    x,avg_acc,avg_diff = learn_to_grow(model,criterion,train_loaders,val_loaders,task_names_sub,
                                    datamode,opt.pretrain_path,lr,freeze_past,task_classes,epochs,opt.sim_strat)
    num_paras = compute_num_para(model)
    save_results(opt,model,x,avg_acc,avg_diff)
    print(viz_jnet(model.sub_graphs))
    return x, avg_acc, avg_diff, num_paras
    

def save_results(opt,model,x,avg_acc,avg_diff):
    num_paras = compute_num_para(model)
    plt.plot(x,avg_acc)
    plt.plot(x,avg_diff)
    plt.legend(["average accuracy", "average mono-diff accuracy"])
    plt.xlabel('tasks')
    plt.ylabel('accuracy')
    plt.title(opt.exp_name)
    save_path = opt.exp_dir+opt.exp_name
    plt.savefig(save_path+'.png')
    dump = OrderedDict()
    dump = {'x':x,'avg_acc':avg_acc,'avg_diff':avg_diff,'sub_graphs':model.sub_graphs,'num_paras':num_paras}
    torch.save(dump,save_path+'res.pth')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--config_name',help='name of Config to be used')
    arg = parser.parse_args()
    config = getattr(M, arg.config_name)
    run_learn_to_grow(config)

