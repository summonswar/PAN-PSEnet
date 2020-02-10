from dataclasses import dataclass, replace
from typing import List

'''@dataclass
class Resources:
    exp_dir: str
    train_data: str
    valid_data: str
    spath: str 
    shared_model: str

@dataclass
class HP:
    lr: float = 1.0
    beta1: float = 0.9
    rho: float = 0.95
    eps: float = 1e-8
    grad_clip: int = 5'''


@dataclass
class Config:

    #re: Resources
    
    experiment_name: str
    exp_dir: str
    train_data: str
    valid_data: str
    langs: List[str]
    pli: List[int]
    mode: List[str]
    #hp: HP
    character: str = None
    shared_model: str = ''
    spath: str = 'saved_models/SharedLSTM_AlternateHinBan_scratch_best_accuracy.pth'
    Transformation: str = 'None'
    FeatureExtraction: str = 'VGG'
    SequenceModeling: str = 'BiLSTM'
    Prediction: str = 'CTC'
    share: str = 'CNN+LSTM'
    manualSeed: int = 1111
    workers: int = 4
    batch_size: int = 192
    num_iter: int = 300000
    valInterval: int = 1000
    saved_model: str = ''
    FT: bool = False
    adam: bool = False
    select_data: str = 'Syn-Real'
    batch_ratio: str = '0.5-0.5'
    total_data_usage_ratio: str = '1.0'
    lr: float = 1.0
    beta1: float = 0.9
    rho: float = 0.95
    eps: float = 1e-8
    grad_clip: int = 5
    batch_max_length: int = 30
    imgH: int = 32
    imgW: int = 100
    rgb: bool = False
    sensitive: bool = False
    PAD: bool = False
    data_filtering_off: bool = False
    num_fiducial: int = 20
    input_channel: int = 1
    output_channel: int = 512
    hidden_size: int = 256

    #def __postinnit__:


#re1 = Resources(exp_dir='Experiments', train_data='training/', valid_data='validation/', spath='lol.pth', shared_model='ha.pth')
#h1 = HP()
C1 = Config(experiment_name = 'TestDataclass', exp_dir = 'Experiments', train_data = 'training/', valid_data = 'validation/', langs = ['ban','hin'], pli = [1000,1000], mode = ['train','train'])
C2 = replace(C1, experiment_name = 'TestDataclass1', langs = ['hin','ban','arab'], pli = [1000,1000,1000], mode = ['train','train','train'])
C3_test = replace(C2, experiment_name = 'ABH(CNN)', pli = [2,2,2], share = 'CNN', total_data_usage_ratio = '0.05')
C3_tst_val = replace(C3_test, mode = ['val','val','val'])
print(C2)


