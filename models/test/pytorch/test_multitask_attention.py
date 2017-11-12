#! /usr/bin/env python
# -*- coding: utf-8 -*-

"""Test multitask Attention-besed models in pytorch."""

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import sys
import time
import unittest

import torch
import torch.nn as nn

sys.path.append('../../../')
from models.pytorch.attention.multitask_attention_seq2seq import MultitaskAttentionSeq2seq
from models.test.data import generate_data, idx2char, idx2word
from utils.measure_time_func import measure_time
from utils.io.variable import np2var, var2np
from utils.evaluation.edit_distance import compute_cer, compute_wer
from utils.training.learning_rate_controller import Controller

torch.manual_seed(2017)


class TestAttention(unittest.TestCase):

    def test(self):
        print("Attention Working check.")

        self.check(encoder_type='lstm', bidirectional=True,
                   decoder_type='lstm')

    @measure_time
    def check(self, encoder_type, bidirectional, decoder_type,
              attention_type='dot_product',
              downsample=False, input_feeding_approach=False):

        print('==================================================')
        print('  encoder_type: %s' % encoder_type)
        print('  bidirectional: %s' % str(bidirectional))
        print('  decoder_type: %s' % decoder_type)
        print('  attention_type: %s' % attention_type)
        print('  downsample: %s' % str(downsample))
        print('  input_feeding_approach: %s' % str(input_feeding_approach))
        print('==================================================')

        # Load batch data
        inputs, labels, labels_sub, _, _, _ = generate_data(
            model='attention',
            label_type='word_char',
            batch_size=2,
            num_stack=1,
            splice=1)

        # Wrap by Variable
        inputs = np2var(inputs)
        labels = np2var(labels, dtype='long')
        labels_sub = np2var(labels_sub, dtype='long')

        sos_index = 11
        eos_index = 12
        sos_index_sub = 27
        eos_index_sub = 28

        # Load model
        model = MultitaskAttentionSeq2seq(
            input_size=inputs.size(-1),
            encoder_type=encoder_type,
            encoder_bidirectional=bidirectional,
            encoder_num_units=256,
            encoder_num_proj=0,
            encoder_num_layers=2,
            encoder_num_layers_sub=1,
            encoder_dropout=0.1,
            attention_type=attention_type,
            attention_dim=128,
            decoder_type=decoder_type,
            decoder_num_units=256,
            decoder_num_proj=128,
            decdoder_num_layers=1,
            decoder_num_units_sub=256,
            decoder_num_proj_sub=128,
            decdoder_num_layers_sub=1,
            decoder_dropout=0.1,
            embedding_dim=64,
            embedding_dim_sub=64,
            embedding_dropout=0.1,
            num_classes=sos_index,
            sos_index=sos_index,
            eos_index=eos_index,
            num_classes_sub=sos_index_sub,
            sos_index_sub=sos_index_sub,
            eos_index_sub=eos_index_sub,
            max_decode_length=30,
            max_decode_length_sub=100,
            splice=1,
            parameter_init=0.1,
            init_dec_state_with_enc_state=True,
            sharpening_factor=1,
            logits_temperature=1,
            sigmoid_smoothing=False,
            input_feeding_approach=input_feeding_approach)

        # Count total parameters
        for name, num_params in model.num_params_dict.items():
            print("%s %d" % (name, num_params))
        print("Total %.3f M parameters" % (model.total_parameters / 1000000))

        # Define optimizer
        optimizer, scheduler = model.set_optimizer(
            'adam',
            learning_rate_init=1e-3,
            weight_decay=1e-6,
            lr_schedule=False,
            factor=0.1,
            patience_epoch=5)

        # Define learning rate controller
        learning_rate = 1e-3
        lr_controller = Controller(
            learning_rate_init=learning_rate,
            decay_start_epoch=20,
            decay_rate=0.9,
            decay_patient_epoch=10,
            lower_better=True)

        # Initialize parameters
        model.init_weights()

        # GPU setting
        use_cuda = model.use_cuda
        model.set_cuda(deterministic=False)
        if use_cuda:
            model = model.cuda()
            inputs = inputs.cuda()
            labels = labels.cuda()
            labels_sub = labels_sub.cuda()

        # Train model
        max_step = 1000
        start_time_step = time.time()
        ler_train_pre = 1
        for step in range(max_step):

            # Clear gradients before
            optimizer.zero_grad()

            # Make prediction
            outputs, att_weights, outputs_sub, att_weights_sub = model(
                inputs, labels, labels_sub)

            # Compute loss
            loss = model.compute_loss(outputs, labels, outputs_sub, labels_sub)

            # Compute gradient
            optimizer.zero_grad()
            loss.backward()

            # Clip gradient norm
            nn.utils.clip_grad_norm(model.parameters(), 10)

            # Update parameters
            if scheduler is not None:
                scheduler.step(ler_train_pre)
            else:
                optimizer.step()

            if (step + 1) % 10 == 0:
                # ***Change to evaluation mode***
                model.eval()

                # Decode
                labels_pred, _ = model.decode_infer(inputs, beam_width=1)
                labels_pred_sub, _ = model.decode_infer_sub(
                    inputs, beam_width=1)

                # Compute accuracy
                str_pred = idx2word(labels_pred[0][0:-1]).split('>')[0]
                str_true = idx2word(var2np(labels)[0][1:-1])
                ler_train = compute_wer(ref=str_true.split('_'),
                                        hyp=str_pred.split('_'),
                                        normalize=True)

                str_pred_sub = idx2char(labels_pred_sub[0][0:-1]).split('>')[0]
                str_true_sub = idx2char(var2np(labels_sub)[0][1:-1])
                # ler_train_sub = compute_cer(ref=str_true_sub.replace('_', ''),
                #                             hyp=str_pred_sub.replace('_', ''),
                #                             normalize=True)

                # ***Change to training mode***
                model.train()

                duration_step = time.time() - start_time_step
                print('Step %d: loss = %.3f / ler = %.3f (%.3f sec) / lr = %.5f' %
                      (step + 1, var2np(loss), ler_train, duration_step, learning_rate))
                start_time_step = time.time()

                # Visualize
                print('Ref: %s' % str_true)
                print('Hyp (word): %s' % str_pred)
                print('Hyp (char): %s' % str_pred_sub)

                if ler_train < 0.1:
                    print('Modle is Converged.')
                    break
                ler_train_pre = ler_train

                # Update learning rate
                optimizer, learning_rate = lr_controller.decay_lr(
                    optimizer=optimizer,
                    learning_rate=learning_rate,
                    epoch=step,
                    value=ler_train)


if __name__ == "__main__":
    unittest.main()
