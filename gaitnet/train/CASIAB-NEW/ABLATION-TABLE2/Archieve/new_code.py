"""
training not include CBG
no weights decay makes synthesize image better
weights decay makes performance better
"""
import random
import os
import numpy as np
import torch
import torchvision
from torchvision import transforms
import torch.nn as nn
import torch.optim as optim
from torchvision.utils import make_grid, save_image
import argparse
from torch.utils.data import DataLoader
from torch.utils.tensorboard import SummaryWriter
# from tensorboardX import SummaryWriter
from PIL import Image
from torch.nn.utils.rnn import pad_sequence
from utils.compute import eval_cmc
import imageio
import torch.backends.cudnn as cudnn
cudnn.benchmark = True
debug_mode = False
#################################################################################################################
# HYPER PARAMETERS INITIALIZING
parser = argparse.ArgumentParser()
gpu_index = []
parser.add_argument('--lr', default=0.0001, type=float, help='learning rate')
parser.add_argument('--data_root',
                    default='/home/tony/Documents/CASIA-B/SEG',
                    # default='/user/zhang835/link2-ziyuan-ssd/FVG-NEW/SEG',
                    help='root directory for data')
parser.add_argument('--seed', default=1, type=int, help='manual seed')
parser.add_argument('--batch_size', default=16, type=int, help='batch size')
parser.add_argument('--em_dim', type=int, default=320, help='size of the pose vector')
parser.add_argument('--fa_dim', type=int, default=288, help='size of the appearance vector')
parser.add_argument('--fg_dim', type=int, default=32, help='size of the gait vector')
parser.add_argument('--im_height', type=int, default=64, help='the height of the input image to network')
parser.add_argument('--im_width', type=int, default=32, help='the width of the input image to network')
parser.add_argument('--max_step', type=int, default=20, help='maximum distance between frames')
parser.add_argument('--savedir', default='./runs')
signature = input('Specify a NAME for this running:')
parser.add_argument('--signature', default=signature)
opt = parser.parse_args()
torch.cuda.set_device(0)

module_save_path = os.path.join(opt.savedir, 'modules', signature)
if os.path.exists(module_save_path):
    model_names = os.listdir(module_save_path)
    model_names = [f for f in model_names if f.endswith('.pickle')]
else:
    model_names = []
if len(model_names):
    model_names.sort(key=lambda a: int(a.split(".")[0]))
    loading_model_path = os.path.join(module_save_path, model_names[-1])
    itr = int(model_names[-1].split('.')[0])
else:
    loading_model_path = None
    itr = 0

print(opt)

print("Random Seed: ", opt.seed)
random.seed(opt.seed)
torch.manual_seed(opt.seed)
torch.cuda.manual_seed_all(opt.seed)

os.makedirs('%s/modules/%s' % (opt.savedir, opt.signature), exist_ok=True)
os.makedirs('%s/gifs/%s' % (opt.savedir, opt.signature), exist_ok=True)


#################################################################################################################
# class CASIAB_instance(object):
#
#     def __init__(self,
#                  folder_path,
#                  frame_height,
#                  frame_width,
#                  ):
#         self.folder_path = folder_path
#         self.frame_names = sorted(os.listdir(self.folder_path))
#         self.frame_names = [f for f in self.frame_names if f.endswith('.png')]
#         self.frame_height = frame_height
#         self.frame_width = frame_width
#         self.transform = transforms.Compose([
#             transforms.ToPILImage(),
#             transforms.Resize((self.frame_height, self.frame_width)),
#             transforms.ToTensor()
#         ])
#
#     def __getitem__(self, index):
#         try:
#             frame = np.asarray(Image.open(os.path.join(self.folder_path,
#                                                        self.frame_names[index])))
#             frame = self.transform(frame)
#         except:
#             frame = torch.zeros(3, self.frame_height, self.frame_width)
#         return frame
#
#     def __len__(self):
#         return len(self.frame_names)


class FVG(object):

    def __init__(self,
                 data_root,
                 clip_len,
                 im_height,
                 im_width,
                 seed,
                 ):

        np.random.seed(seed)
        self.is_train_data = True
        self.data_root = data_root
        self.clip_len = clip_len
        self.im_height = im_height
        self.im_width = im_width
        self.transform = transforms.Compose([
            transforms.ToPILImage(),
            transforms.Resize((self.im_height, self.im_width)),
            transforms.ToTensor()
        ])
        self.im_shape = [self.clip_len, 3, self.im_height, self.im_width]

        subjects = list(range(1, 124 + 1))
        self.train_subjects = subjects[:74]
        self.test_subjects = subjects[74:]
        print('training_subjects', self.train_subjects, len(self.train_subjects))
        print('testing_subjects', self.test_subjects, len(self.test_subjects))

    def get_eval_data(self,
                      is_train_data,
                      # gallery,
                      # probe,
                      ):
        if is_train_data is True:
            subjects = self.train_subjects
        else:
            subjects = self.test_subjects

        def read_frames(si, con, sei, vi):

            in_path = os.path.join(self.data_root, '%03d-%s-%02d-%03d' % (si, con, sei, vi))
            fvg_instance = CASIAB_instance(folder_path=in_path,
                                           frame_height=self.im_height,
                                           frame_width=self.im_width)
            loader = DataLoader(fvg_instance,
                                num_workers=8,
                                batch_size=30,
                                shuffle=False,
                                drop_last=False,
                                pin_memory=True)

            data = []
            for batch_frame in loader:
                batch_frame = batch_frame.cuda()
                data.append(batch_frame)
            if len(data):
                data = torch.cat(data)
            else:
                data = torch.zeros([3, self.im_height, self.im_height])
            return data

        test_data_glr = []
        test_data_prb = []

        conditions = ['nm', 'cl']
        ###########################################################
        # gallry
        for vi in [90]:
            glr_this_view = []
            for i, id in enumerate(subjects):
                print(vi, i)
                glr_this_view.append(read_frames(id, conditions[0], 1, vi))
            glr_this_view = pad_sequence(glr_this_view, False)
            glr_this_view = glr_this_view[:70]
            test_data_glr.append(glr_this_view)

        ###########################################################
        # probe
        for vi in [90]:
            prb_this_view = []
            for i, id in enumerate(subjects):
                print(vi, i)
                prb_this_view.append(read_frames(id, conditions[1], 1, vi))
            prb_this_view = pad_sequence(prb_this_view, False)
            prb_this_view = prb_this_view[:70]
            test_data_prb.append(prb_this_view)

        # test_data_glr = torch.tensor(test_data_glr).permute(0, 2, 1, 3, 4, 5)
        # test_data_prb = torch.tensor(test_data_prb).permute(0, 2, 1, 3, 4, 5)

        # test_data_glr = pad_sequence(test_data_glr)
        # test_data_glr = test_data_glr[:70]
        # test_data_prb = pad_sequence(test_data_prb)
        # test_data_prb = test_data_prb[:70]
        return test_data_glr, test_data_prb

    def __getitem__(self, index):
        """
        chose any two videos for the same subject
        :param index:
        :return:
        """
        data_structure = {
            'nm': list(range(1, 6 + 1)),
            'cl': list(range(1, 2 + 1)),
            'bg': list(range(1, 2 + 1)),
        }

        # 30 x 3 x 32 x 64
        def random_chose():
            if self.is_train_data:
                si_idx = np.random.choice(self.train_subjects)
                label = self.train_subjects.index(si_idx)
            else:

                si_idx = np.random.choice(self.test_subjects)
                label = self.test_subjects.index(si_idx)

            cond1 = np.random.choice(['nm', 'cl'])
            senum1 = np.random.choice(data_structure[cond1])
            cond2 = np.random.choice(['nm', 'cl'])
            senum2 = np.random.choice(data_structure[cond2])

            # view_idx1 = np.random.choice(list(range(0, 180 + 1, 18)))
            # view_idx2 = np.random.choice(list(range(0, 180 + 1, 18)))

            view_idx1 = np.random.choice([90])
            view_idx2 = np.random.choice([90])

            return si_idx, (cond1, senum1, view_idx1), (cond2, senum2, view_idx2), label

        def random_length(dirt, length):
            files = sorted(os.listdir(dirt))
            num = len(files)
            if num - length < 2:
                return None
            start = np.random.randint(1, num - length)
            end = start + length
            return files[start:end]

        def read_frames(frames_pth, file_names):
            # frames = np.zeros(self.im_shape, np.float32)
            frames = []
            for f in file_names:
                frame = np.asarray(Image.open(os.path.join(frames_pth, f)))
                frame = self.transform(frame)
                frames.append(frame)
            frames = torch.stack(frames)
            return frames

        si, param1, param2, label = random_chose()
        frames_pth1 = os.path.join(self.data_root,
                                   '%03d-%s-%02d-%03d' % (si,
                                                          param1[0],
                                                          param1[1],
                                                          param1[2]))
        frames_pth2 = os.path.join(self.data_root,
                                   '%03d-%s-%02d-%03d' % (si,
                                                          param2[0],
                                                          param2[1],
                                                          param2[2]))
        file_names1 = random_length(frames_pth1, self.clip_len)
        file_names2 = random_length(frames_pth2, self.clip_len)

        while True:
            if file_names1 == None or file_names2 == None:
                si, param1, param2, label = random_chose()
                frames_pth1 = os.path.join(self.data_root,
                                           '%03d-%s-%02d-%03d' % (si,
                                                                  param1[0],
                                                                  param1[1],
                                                                  param1[2]))
                frames_pth2 = os.path.join(self.data_root,
                                           '%03d-%s-%02d-%03d' % (si,
                                                                  param2[0],
                                                                  param2[1],
                                                                  param2[2]))
                file_names1 = random_length(frames_pth1, self.clip_len)
                file_names2 = random_length(frames_pth2, self.clip_len)
            else:
                break

        data1 = read_frames(frames_pth1, file_names1)
        data2 = read_frames(frames_pth2, file_names2)

        return data1, data2, label

    def __len__(self):
        if self.is_train_data:
            return len(self.train_subjects) * 110
        else:
            return len(self.test_subjects) * 110


# #################################################################################################################
# DATASET PREPARATION


fvg = FVG(
    data_root=opt.data_root,
    clip_len=opt.max_step,
    im_height=opt.im_height,
    im_width=opt.im_width,
    seed=opt.seed
)
data_loader = DataLoader(fvg,
                         num_workers=8,
                         batch_size=opt.batch_size,
                         shuffle=True,
                         drop_last=True,
                         pin_memory=True)
def get_batch():
    while True:
        for batch in data_loader:
            batch = [e.cuda() for e in batch]
            yield batch


training_batch_generator = get_batch()

# #################################################################################################################

# def init_weights(m):
#     classname = m.__class__.__name__
#     if classname.find('Conv') != -1 or classname.find('Linear') != -1:
#         m.weight.data.normal_(0.0, 0.02)
#         m.bias.data.fill_(0)
#     elif classname.find('BatchNorm') != -1:
#         m.weight.data.normal_(1.0, 0.02)
#         m.bias.data.fill_(0)

def adjust_white_balance(x):
    # x = x.cpu()
    avgR = np.average(x[0, :, :])
    avgG = np.average(x[1, :, :])
    avgB = np.average(x[2, :, :])

    avg = (avgB + avgG + avgR) / 3

    x[0, :, :] = np.minimum(x[0] * (avg / avgR), 1)
    x[1, :, :] = np.minimum(x[1] * (avg / avgG), 1)
    x[2, :, :] = np.minimum(x[2] * (avg / avgB), 1)

    return x


def adjust_(x):
    x = transforms.functional.adjust_brightness(x, 1.5)
    x = transforms.functional.adjust_contrast(x, 1.5)
    return x


#################################################################################################################

class vgg_layer(nn.Module):
    def __init__(self, nin, nout):
        super(vgg_layer, self).__init__()
        self.main = nn.Sequential(
            nn.Conv2d(nin, nout, 3, 1, 1),
            nn.BatchNorm2d(nout),
            nn.LeakyReLU(0.2)
        )

    def forward(self, input):
        return self.main(input)


class dcgan_conv(nn.Module):
    def __init__(self, nin, nout):
        super(dcgan_conv, self).__init__()
        self.main = nn.Sequential(
            nn.Conv2d(nin, nout, 4, 2, 1
                      ),
            nn.BatchNorm2d(nout),
            nn.LeakyReLU(0.2),
        )

    def forward(self, input):
        return self.main(input)


class dcgan_upconv(nn.Module):
    def __init__(self, nin, nout):
        super(dcgan_upconv, self).__init__()
        self.main = nn.Sequential(
            nn.ConvTranspose2d(nin, nout, 4, 2, 1, ),
            nn.BatchNorm2d(nout),
            nn.LeakyReLU(0.2),
        )

    def forward(self, input):
        return self.main(input)


class encoder(nn.Module):
    def __init__(self, nc=3):
        super(encoder, self).__init__()
        self.em_dim = opt.em_dim
        nf = 64
        self.main = nn.Sequential(
            dcgan_conv(nc, nf),
            # vgg_layer(nf, nf),

            dcgan_conv(nf, nf * 2),
            # vgg_layer(nf * 2, nf * 2),

            dcgan_conv(nf * 2, nf * 4),
            # vgg_layer(nf * 4, nf * 4),

            dcgan_conv(nf * 4, nf * 8),
            # vgg_layer(nf * 8, nf * 8),

            # nn.Conv1d(nf * 8, self.em_dim, 4, 1, 0),
            # nn.BatchNorm2d(self.em_dim),
        )

        self.flatten = nn.Sequential(
            nn.Linear(nf * 8 * 2 * 4, self.em_dim),
            nn.BatchNorm1d(self.em_dim),
        )

    def forward(self, input):
        embedding = self.main(input).view(-1, 64 * 8 * 2 * 4)
        embedding = self.flatten(embedding)
        fa, fg = torch.split(embedding, [opt.fa_dim, opt.fg_dim], dim=1)
        return fa, fg, embedding


class decoder(nn.Module):
    def __init__(self, nc=3):
        super(decoder, self).__init__()
        nf = 64
        self.em_dim = opt.em_dim

        self.trans = nn.Sequential(
            nn.Linear(self.em_dim, nf * 8 * 2 * 4),
            nn.BatchNorm1d(nf * 8 * 2 * 4),
        )

        self.main = nn.Sequential(
            # nn.ConvTranspose2d(self.em_dim, nf * 8, 4, 1, 0),
            # nn.BatchNorm2d(nf * 8),
            nn.LeakyReLU(0.2),
            # vgg_layer(nf * 8, nf * 8),

            dcgan_upconv(nf * 8, nf * 4),
            # vgg_layer(nf * 4, nf * 4),

            dcgan_upconv(nf * 4, nf * 2),
            # vgg_layer(nf * 2, nf * 2),

            dcgan_upconv(nf * 2, nf),
            # vgg_layer(nf, nf),

            nn.ConvTranspose2d(nf, nc, 4, 2, 1),
            nn.Sigmoid()
            # because image pixels are from 0-1, 0-255
        )

    def forward(self, fa, fg):
        hidden = torch.cat([fa, fg], 1).view(-1, opt.em_dim)
        small = self.trans(hidden).view(-1, 64 * 8, 4, 2)
        img = self.main(small)
        return img


class lstm(nn.Module):
    def __init__(self, hidden_dim=128, tagset_size=74):
        super(lstm, self).__init__()
        self.source_dim = opt.fg_dim
        self.hidden_dim = hidden_dim
        self.tagset_size = tagset_size
        self.lens = 0
        self.lstm = nn.LSTM(self.source_dim, hidden_dim, 3)
        self.fc1 = nn.Sequential(
            nn.BatchNorm1d(hidden_dim),
            nn.Linear(hidden_dim, hidden_dim),
            nn.BatchNorm1d(hidden_dim),
            nn.LeakyReLU(0.2),

        )
        self.main = nn.Sequential(
            # nn.Dropout(),
            nn.Linear(self.hidden_dim, tagset_size),
            nn.BatchNorm1d(tagset_size),
        )

    def forward(self, batch):
        lens = batch.shape[0]
        lstm_out, _ = self.lstm(batch.view(lens, -1, self.source_dim))
        lstm_out_test = self.fc1(torch.mean(lstm_out.view(lens, -1, self.hidden_dim), 0))
        lstm_out_train = self.main(lstm_out_test).view(-1, self.tagset_size)
        return lstm_out_train, lstm_out_test, lstm_out


#################################################################################################################
# MODEL PROCESS
netE = encoder()
netD = decoder()
lstm = lstm()
# netE.apply(init_weights)
# netD.apply(init_weights)
# lstm.apply(init_weights)
if loading_model_path:
    checkpoint = torch.load(loading_model_path)
    netE.load_state_dict(checkpoint['netE'])
    netD.load_state_dict(checkpoint['netD'])
    lstm.load_state_dict(checkpoint['lstm'])
    print('MODEL LOADING SUCCESSFULLY:', loading_model_path)

# device = torch.device("cuda:0" if torch.cuda.is_available() else "cpu")
# if torch.cuda.device_count() > 1:
#   print("Let's use", torch.cuda.device_count(), "GPUs!")
#   netE = nn.DataParallel(netE)
#   netD = nn.DataParallel(netD)
#   lstm = nn.DataParallel(lstm)

# netE.to(device)
# netD.to(device)
# lstm.to(device)

# optimizerE = optim.Adam(netE.parameters(), lr=opt.lr, betas=(0.9, 0.999), weight_decay=0.001)
# optimizerD = optim.Adam(netD.parameters(), lr=opt.lr, betas=(0.9, 0.999), weight_decay=0.001)
# optimizerLstm = optim.Adam(lstm.parameters(), lr=opt.lr, betas=(0.9, 0.999), weight_decay=0.001)

optimizerE = optim.Adam(netE.parameters(), lr=opt.lr, betas=(0.9, 0.999))
optimizerD = optim.Adam(netD.parameters(), lr=opt.lr, betas=(0.9, 0.999))
optimizerLstm = optim.Adam(lstm.parameters(), lr=opt.lr, betas=(0.9, 0.999))

mse_loss = nn.MSELoss()
bce_loss = nn.BCELoss()
cse_loss = nn.CrossEntropyLoss()
# trp_loss = nn.TripletMarginLoss(margin=2.0)
netE.cuda()
netD.cuda()
lstm.cuda()
mse_loss.cuda()
bce_loss.cuda()
cse_loss.cuda()


# trp_loss.cuda()

#################################################################################################################

def make_analogy_inter_subs(x, step, opt):
    # (B, L, C, H, W)
    # (   B, C, H, W)

    none = torch.zeros([1, 3, opt.im_height, opt.im_width]).cuda()
    x_gs = torch.stack([i for i in [x[0][step], x[1][step], x[2][step], x[3][step]]]).cuda()
    h_gs = netE(x_gs)[1]
    # h_gs = torch.zeros(4,32).cuda()

    x_as = torch.stack([x[i][step] for i in [1, 2, 11]]).cuda()
    # x_as = torch.stack([x[i][0] for i in [2, 2, 2, 2, 2]]).cuda()

    h_as = netE(x_as)[0]
    # h_as = torch.zeros(3,288).cuda()

    gene = [netD(torch.stack([i] * 4).cuda(), h_gs) for i in h_as]
    row0 = torch.cat([none, x_gs])
    rows = [torch.cat([e.unsqueeze(0), gene[i]]) for i, e in enumerate(x_as)]
    to_plot = torch.cat([row0] + rows)

    img = make_grid(to_plot, 5)
    return img

def make_analogy_intal_subs(nm, cl, idx):
    # (B, L, C, H, W)
    # (   B, C, H, W)
    # netE.eval()
    # netD.eval()

    def rand_idx():
        return 0

    def rand_step():
        # return np.random.randint(0, opt.max_step)
        return 0

    none = torch.zeros([1, 3, 64, 32]).cuda()
    x_gs = torch.stack([i for i in [nm[idx][0], nm[idx][4], nm[idx][8], nm[idx][15]]]).cuda()
    h_gs = netE(x_gs)[1]
    # h_gs = torch.zeros(4,32).cuda()

    x_as = torch.stack([nm[idx][rand_step()], cl[idx][rand_step()]]).cuda()
    # x_as = torch.stack([x[i][0] for i in [2, 2, 2, 2, 2]]).cuda()

    h_as = netE(x_as)[0]
    # h_as = torch.zeros(2,288).cuda()

    gene = [netD(torch.stack([i] * 4).cuda(), h_gs) for i in h_as]
    row0 = torch.cat([none, x_gs])
    rows = [torch.cat([e.unsqueeze(0), gene[i]]) for i, e in enumerate(x_as)]
    to_plot = torch.cat([row0] + rows)

    img = make_grid(to_plot, 5)
    return img


def plot_anology(data, itr):
    frames = []
    for step in range(data.shape[1]):
        # frame = data[:,step:step+1,:,:,:]
        anology_frame = make_analogy_inter_subs(data, step, opt)
        anology_frame = anology_frame.cpu().numpy()
        anology_frame = np.transpose(anology_frame, (1, 2, 0))
        # anology_frame = adjust_white_balance(anology_frame.cpu().numpy())
        frames.append(anology_frame)
    imageio.mimsave("{:s}/gifs/{:s}/{:d}.gif".format(opt.savedir, opt.signature, itr), frames)

    # all = torch.cat([anology1, anology2], dim=1)

    # writer.add_image('inter_sub', all, epoch)

    # anology1 = make_analogy_intal_subs(data1, data2, 8)
    # anology2 = make_analogy_intal_subs(data1, data2, 9)
    # all = torch.cat([anology1, anology2], dim=1)
    # all = adjust_white_balance(all.detach())
    # writer.add_image('intral_sub', all, epoch)


def write_tfboard(vals, itr, name):
    for idx, item in enumerate(vals):
        writer.add_scalar('data/%s%d' % (name, idx), torch.tensor(item), itr)


#################################################################################################################
# TRAINING FUNCTION DEFINE
def train_main(Xa, Xb, l):
    Xa, Xb = Xa.transpose(0, 1), Xb.transpose(0, 1)
    fgs_a = []
    fgs_b = []

    xrec_loss = 0
    for i in range(0, len(Xa)):
        netE.zero_grad()
        netD.zero_grad()
        lstm.zero_grad()
        rdm = torch.LongTensor(1).random_(0, len(Xa))[0]
        # ------------------------------------------------------------------
        xa1, xa2 = Xa[rdm], Xa[i]
        fa1, fg1, em1 = netE(xa1)
        fa2, fg2, em2 = netE(xa2)
        # ------------------------------------------------------------------
        xa2_ = netD(fa1, fg2)
        xrec_loss += mse_loss(xa2_, xa2)
        # ------------------------------------------------------------------

        xai, xbi = Xa[i], Xb[i]
        _, fgai, _ = netE(xai)
        _, fgbi, _ = netE(xbi)

        fgs_a.append(fgai)
        fgs_b.append(fgbi)

    fgs_a = torch.stack(fgs_a)
    fgs_a = torch.mean(fgs_a, 0)

    fgs_b = torch.stack(fgs_b)
    fgs_b = torch.mean(fgs_b, 0)

    gait_sim_loss = mse_loss(fgs_a, fgs_b) * 0.01

    loss = xrec_loss + gait_sim_loss

    loss.backward()
    optimizerE.step()
    optimizerD.step()

    return [xrec_loss.item(), gait_sim_loss.item()]


def train_lstm(X, l):
    X = X.transpose(0, 1)
    id_incre_loss = 0
    fgs = []
    for i in range(0, len(X)):
        netE.zero_grad()
        netD.zero_grad()
        lstm.zero_grad()

        xi = X[i]
        fgs.append(netE(xi)[1])
        lstm_out_train = lstm(torch.stack(fgs))[0]
        id_incre_loss += cse_loss(lstm_out_train, l)

    id_incre_loss /= opt.max_step
    id_incre_loss *= 0.1

    los = id_incre_loss
    los.backward()
    optimizerLstm.step()
    optimizerE.step()
    return [los.item()]


#################################################################################################################
# FUN TRAINING TIME !


# proto_WS = np.load('testset_WS.npy', allow_pickle=True)
# proto_WS = torch.tensor(proto_WS[0]).cuda(), torch.tensor(proto_WS[1]).cuda(), proto_WS[2]
# proto_CB = np.load('testset_CB.npy', allow_pickle=True)
# proto_CB = torch.tensor(proto_CB[0]).cuda(), torch.tensor(proto_CB[1]).cuda(), proto_CB[2]
# proto_CL = np.load('testset_CL.npy', allow_pickle=True)
# proto_CL = torch.tensor(proto_CL[0]).cuda(), torch.tensor(proto_CL[1]).cuda(), proto_CL[2]

# proto_WS = fvg.get_eval_data(False, 2, probe_session1=list(range(4, 9 + 1)), probe_session2=list(range(4, 6 + 1)))
# proto_CB = fvg.get_eval_data(False, 2, probe_session1=list(range(10, 12 + 1)), probe_session2=[])
proto_CL = fvg.get_eval_data(False)
# proto_CBG = fvg.get_eval_data(False, 2, probe_session1=list(range(10, 12 + 1)))


if not debug_mode:

    writer = SummaryWriter('%s/logs/%s' % (opt.savedir, opt.signature))

    while True:
        netE.train()
        netD.train()
        lstm.train()

        batch_cond1, batch_cond2, lb = next(training_batch_generator)
        # batch_cond1 = batch_cond1.to(device)
        # batch_cond2 = batch_cond2.to(device)
        # lb = lb.to(device)

        xrec_loss, gait_sim_loss = train_main(batch_cond1, batch_cond2, lb)
        write_tfboard([xrec_loss], itr, name='xrec_loss')
        write_tfboard([gait_sim_loss], itr, name='gait_sim_loss')
        id_incre_loss = train_lstm(batch_cond1, lb)
        write_tfboard(id_incre_loss, itr, name='id_incre_loss')
        print(itr)

        # ----------------EVAL()--------------------
        if itr % 50 == 0 and itr != 0:
            with torch.no_grad():
                netD.eval()
                netE.eval()
                lstm.eval()
                # eval_WS = eval_lstm_roc(proto_WS[0], proto_WS[1], proto_WS[2], 90, [netE, lstm], opt)
                # write_tfboard(eval_WS[:2], itr, name='WS')
                # eval_CB = eval_lstm_roc(proto_CB[0], proto_CB[1], proto_CB[2], 90, [netE, lstm], opt)
                # write_tfboard(eval_CB[:2], itr, name='CB')
                eval_CL = eval_cmc(proto_CL[0], proto_CL[1], [netE, lstm], opt, [90], [90], True)
                write_tfboard(eval_CL, itr, name='CL')

                # batch_cond1, batch_cond2, _ = next(testing_batch_generator)
                plot_anology(batch_cond1, itr)
        # ----------------SAVE MODEL--------------------
        if itr % 1000 == 0 and itr != 0:
            torch.save({
                'netD': netD.state_dict(),
                'netE': netE.state_dict(),
                'lstm': lstm.state_dict(),
            },
                '%s/modules/%s/%d.pickle' % (opt.savedir, opt.signature, itr), )

        itr += 1

else:
    with torch.no_grad():
        netD.eval()
        netE.eval()
        lstm.eval()
        # eval_WS = eval_lstm_roc(proto_WS[0], proto_WS[1], proto_WS[2], 90, [netE, lstm], opt)
        # print(eval_WS)
        # # write_tfboard(eval_WS[:2], itr, name='WS')
        # eval_CB = eval_lstm_roc(proto_CB[0], proto_CB[1], proto_CB[2], 90, [netE, lstm], opt)
        # print(eval_CB)
        # # write_tfboard(eval_CB[:2], itr, name='CB')
        eval_CL = eval_cmc(proto_CL[0], proto_CL[1], [netE, lstm], opt, [90], [90], True)
        print(eval_CL)
        # write_tfboard(eval_CL[:2], itr, name='CL')
        # writer.close()
        # batch_cond1, batch_cond2, _ = next(testing_batch_generator)
        # plot_anology(batch_cond1, batch_cond2, itr)
