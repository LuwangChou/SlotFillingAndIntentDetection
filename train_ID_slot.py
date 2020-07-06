import os
import argparse
import logging
import sys
import tensorflow as tf
import numpy as np
from tensorflow.contrib.rnn.python.ops import core_rnn_cell
from tensorflow.python.ops import rnn_cell_impl

from utils import createVocabulary, loadVocabulary, computeF1Score, DataProcessor
from utils import prepareEmbeddingsMatrix
os.environ["CUDA_VISIBLE_DEVICES"] = "0"
from tensorflow.compat.v1 import ConfigProto
from tensorflow.compat.v1 import InteractiveSession



# tf.set_random_seed(20181226) 
# np.random.seed(20181226)
# todo: 1. word pre-train embedding, gru, crf, lr decay

parser = argparse.ArgumentParser(allow_abbrev=False)

# Network
parser.add_argument("--num_units", type=int, default=64, help="Network size.", dest='layer_size')
parser.add_argument("--model_type", type=str, default='full', help="""full(default) | intent_only
                                                                    full: full attention model
                                                                    intent_only: intent attention model
                                                                    slot_only: slot attention model 
                                                                    none: not using any attention""")
parser.add_argument("--use_crf", type=bool, default=False, help="""use crf for seq labeling""")
parser.add_argument("--use_embedding", type=str, default='1', help="""use pre-trained embedding""")
parser.add_argument("--cell", type=str, default='rnn', help="""rnn cell""")  

# Training Environment
parser.add_argument("--batch_size", type=int, default=16, help="Batch size.")
parser.add_argument("--batch_size_add", type=int, default=4, help="Batch size add.")
parser.add_argument("--max_epochs", type=int, default=100, help="Max epochs to train.")
parser.add_argument("--no_early_stop", action='store_false', dest='early_stop',
                    help="Disable early stop, which is based on sentence level accuracy.")  
parser.add_argument("--patience", type=int, default=15, help="Patience to wait before stop.")
# learn rate param
parser.add_argument("--learning_rate_decay", type=str, default='1', help="learning_rate_decay")
parser.add_argument("--learning_rate", type=float, default=0.001, help="The initial learning rate.")
parser.add_argument("--decay_steps", type=int, default=280 * 4, help="decay_steps.")
parser.add_argument("--decay_rate", type=float, default=0.9, help="decay_rate.")

# Model and Vocab
parser.add_argument("--dataset", type=str, default='atis', help="""Type 'atis' or 'snips' to use dataset provided by us or enter what ever you named your own dataset.
                Note, if you don't want to use this part, enter --dataset=''. It can not be None""")
parser.add_argument("--model_path", type=str, default='./model', help="Path to save model.")
parser.add_argument("--vocab_path", type=str, default='./vocab', help="Path to vocabulary files.")

# Data
parser.add_argument("--train_data_path", type=str, default='train', help="Path to training data files.")
parser.add_argument("--test_data_path", type=str, default='test', help="Path to testing data files.")
parser.add_argument("--valid_data_path", type=str, default='valid', help="Path to validation data files.")
parser.add_argument("--input_file", type=str, default='seq.in', help="Input file name.")
parser.add_argument("--slot_file", type=str, default='seq.out', help="Slot file name.")
parser.add_argument("--intent_file", type=str, default='label', help="Intent file name.")
parser.add_argument("--embedding_path", type=str, default='', help=" ./wordembedding/ embedding array's path.")

arg = parser.parse_args()
'''
if arg.dataset=='atis':
    arg.model_type='intent_only'
else:
    arg.model_type='full'
'''
# Print arguments
for k, v in sorted(vars(arg).items()):
    print(k, '=', v)
print()
# use full attention or intent only
if arg.model_type == 'full':
    remove_slot_attn = False
    remove_intent_attn = False
elif arg.model_type == 'intent_only':
    remove_slot_attn = True
    remove_intent_attn = False
elif arg.model_type == 'slot_only':
    remove_slot_attn = False
    remove_intent_attn = True
elif arg.model_type == 'none':
    remove_slot_attn = True
    remove_intent_attn = True
else:
    print('unknown model type!')
    exit(1)

# full path to data will be: ./data + dataset + train/test/valid
if arg.dataset == None:
    print('name of dataset can not be None')
    exit(1)
elif arg.dataset == 'snips':
    print('use snips dataset')
elif arg.dataset == 'atis':
    print('use atis dataset')
else:
    print('use own dataset: ', arg.dataset)
full_train_path = os.path.join('./data', arg.dataset, arg.train_data_path)
full_test_path = os.path.join('./data', arg.dataset, arg.test_data_path)
full_valid_path = os.path.join('./data', arg.dataset, arg.valid_data_path)

createVocabulary(os.path.join(full_train_path, arg.input_file), os.path.join(arg.vocab_path, 'in_vocab'))
createVocabulary(os.path.join(full_train_path, arg.slot_file), os.path.join(arg.vocab_path, 'slot_vocab'))
createVocabulary(os.path.join(full_train_path, arg.intent_file), os.path.join(arg.vocab_path, 'intent_vocab'),
                 no_pad=True)
# return map: {'vocab': vocab, 'rev': rev}, vocab: map, rev: array
in_vocab = loadVocabulary(os.path.join(arg.vocab_path, 'in_vocab'))
slot_vocab = loadVocabulary(os.path.join(arg.vocab_path, 'slot_vocab'))
intent_vocab = loadVocabulary(os.path.join(arg.vocab_path, 'intent_vocab'))



def createModel(input_data, input_size, sequence_length, slots, slot_size, intent_size, layer_size,
                isTraining=True):

    if 'rnn'==arg.cell:
        print('using rnn cell')
        cell_fw = tf.contrib.rnn.BasicRNNCell(layer_size)
        cell_bw = tf.contrib.rnn.BasicRNNCell(layer_size)
    elif 'lstm'==arg.cell:
        print('using lstm cell')
        cell_fw = tf.contrib.rnn.BasicLSTMCell(layer_size)
        cell_bw = tf.contrib.rnn.BasicLSTMCell(layer_size)
    elif 'gru'==arg.cell:
        print('using gru cell')
        cell_fw = tf.contrib.rnn.GRUCell(layer_size)
        cell_bw = tf.contrib.rnn.GRUCell(layer_size)
        
        
    embeddings_size = 100
    if isTraining == True:
        cell_fw = tf.contrib.rnn.DropoutWrapper(cell_fw, input_keep_prob=0.5,
                                                output_keep_prob=0.5)
        cell_bw = tf.contrib.rnn.DropoutWrapper(cell_bw, input_keep_prob=0.5,
                                                output_keep_prob=0.5)
    # embedding layer， [word size, embed size] 724, 64
    if arg.embedding_path=='':
        print("using random uniform embedding!")  
        #embedding = tf.get_variable('embedding', [input_size, layer_size],initializer=tf.random_normal_initializer)
        embedding = tf.get_variable('embedding', [input_size, embeddings_size],initializer=tf.random_uniform_initializer)
        #print(embedding.shape) #[869,64]
        #print(embedding)
        
    else:
        print("using glove embedding!")  
        embedding_weight = prepareEmbeddingsMatrix(arg.embedding_path,in_vocab,embeddings_size)
        print(embedding_weight.shape)
        embedding = tf.Variable(embedding_weight, name='embedding', dtype=tf.float32)
        #print(embedding_weight)
        #print(embedding.shape)[869,64]
    # [bs, nstep, embed size]

    inputs = tf.nn.embedding_lookup(embedding, input_data)
    #print(inputs)
    # state_outputs: [bs, nstep, embed size], final_state: [4, bs, embed size] include cell state * 2, hidden state * 2
    state_outputs, final_state = tf.nn.bidirectional_dynamic_rnn(cell_fw, cell_bw, inputs,
                                                                 sequence_length=sequence_length, dtype=tf.float32)
    # [bs, embed size * 4]
    #print(final_state[0][0].shape)
    if 'lstm'==arg.cell:
        final_state = tf.concat([final_state[0][0], final_state[0][1], final_state[1][0], final_state[1][1]], 1)
    elif 'rnn'==arg.cell or 'gru'==arg.cell:
        final_state = tf.concat([final_state[0], final_state[0], final_state[1], final_state[1]], 1) #for GRU RNN
    # [bs, nstep, embed size * 2]
    state_outputs = tf.concat([state_outputs[0], state_outputs[1]], 2)
    state_shape = state_outputs.get_shape()
    
    with tf.variable_scope('attention'):
        # [bs, nstep, embed size * 2]
        slot_inputs = state_outputs 
        if not remove_slot_attn:
            with tf.variable_scope('slot_attn'):
                # embed size * 2
                attn_size = state_shape[2].value
                origin_shape = tf.shape(state_outputs) 
                # [bs, 1, nstep, embed size * 2]
                hidden = tf.expand_dims(state_outputs, 1)
                # [bs, nstep, 1, embed size * 2]
                hidden_conv = tf.expand_dims(state_outputs, 2)
                # k: [filter_height, filter_width, in_channels, out_channels]
                k = tf.get_variable("AttnW", [1, 1, attn_size, attn_size])
                # [bs, nstep, 1, embed size * 2]
                hidden_features = tf.nn.conv2d(hidden_conv, k, [1, 1, 1, 1], "SAME") 
                # [bs, nstep, embed size * 2]
                hidden_features = tf.reshape(hidden_features, origin_shape)
                # [bs, 1, nstep, embed size * 2]
                hidden_features = tf.expand_dims(hidden_features, 1)
                v = tf.get_variable("AttnV", [attn_size])

                slot_inputs_shape = tf.shape(slot_inputs)
                # [bs * nstep, embed size * 2]
                slot_inputs = tf.reshape(slot_inputs, [-1, attn_size])
                # [bs * nstep, embed size * 2]
                y = core_rnn_cell._linear(slot_inputs, attn_size, True)
                # [bs , nstep, embed size * 2]
                y = tf.reshape(y, slot_inputs_shape)
                # [bs , nstep, 1, embed size * 2]
                y = tf.expand_dims(y, 2)
                # [bs , nstep, nstep] = [bs, 1, nstep, hidden size] + [bs , nstep, 1, embed size * 2]
                s = tf.reduce_sum(v * tf.tanh(hidden_features + y), [3])
                a = tf.nn.softmax(s)
                # a shape = [bs, nstep, nstep, 1]
                a = tf.expand_dims(a, -1)
                # a shape = [bs, nstep, embed size * 2]
                slot_d = tf.reduce_sum(a * hidden, [2])
                slot_output = tf.reshape(slot_d,[-1,attn_size])
        else:
            attn_size = state_shape[2].value
            slot_d=state_outputs
            slot_inputs = tf.reshape(slot_inputs, [-1, attn_size])
            slot_output = slot_inputs

        intent_input = final_state
        if not remove_intent_attn:
            with tf.variable_scope('intent_attn'):
                attn_size = state_shape[2].value  
                # [bs, nstep, 1, embed size * 2]
                hidden = tf.expand_dims(state_outputs, 2)
                k = tf.get_variable("AttnW", [1, 1, attn_size, attn_size])
                # [bs, nstep, 1, embed size * 2]
                hidden_features = tf.nn.conv2d(hidden, k, [1, 1, 1, 1], "SAME")
                v = tf.get_variable("AttnV", [attn_size])
    
                # [bs, embed size * 2]
                y = core_rnn_cell._linear(intent_input, attn_size, True)
                # [bs, 1, 1, embed size * 2]
                y = tf.reshape(y, [-1, 1, 1, attn_size])
                # [bs, nstep]
                s = tf.reduce_sum(v * tf.tanh(hidden_features + y), [2, 3])  
                a = tf.nn.softmax(s)
                # [bs, nstep, 1]
                a = tf.expand_dims(a, -1)
                # [bs, nstep, 1, 1]
                a = tf.expand_dims(a, -1)
                # [bs, embed size * 2]
                d = tf.reduce_sum(a * hidden, [1, 2])
                intent_output = d
                #print('intent_output.shape')
                #print(intent_output.shape)
                #[bs, embedding * 2]
                intent_context_states = intent_output
        else:
            #print('attention size')
            #print(attn_size)
            intent_output = tf.reshape(intent_input, [-1, attn_size])
            intent_output = intent_input
            print(intent_size)
       

    with tf.variable_scope('intent_proj'):
        # [bs, intent_size]
        intent = core_rnn_cell._linear(intent_output, intent_size, True)
    with tf.variable_scope('slot_proj'):
        # [bs * nsetp, intent_size]
        slot = core_rnn_cell._linear(slot_output, slot_size, True)
        if arg.use_crf:
            nstep = tf.shape(state_outputs)[1]
            slot = tf.reshape(slot, [-1, nstep, slot_size])
            # [bs,nstep,slot_size]
    outputs = [slot, intent]
    return outputs


# Create Training Model
input_data = tf.placeholder(tf.int32, [None, None], name='inputs') 
sequence_length = tf.placeholder(tf.int32, [None], name="sequence_length")
global_step = tf.Variable(0, trainable=False, name='global_step')
slots = tf.placeholder(tf.int32, [None, None], name='slots')
slot_weights = tf.placeholder(tf.float32, [None, None], name='slot_weights')
intent = tf.placeholder(tf.int32, [None], name='intent')

with tf.variable_scope('model'):
    training_outputs = createModel(input_data, len(in_vocab['vocab']), sequence_length, slots, len(slot_vocab['vocab']),
                                   len(intent_vocab['vocab']), layer_size=arg.layer_size)

slots_shape = tf.shape(slots)
slots_reshape = tf.reshape(slots, [-1])

slot_outputs = training_outputs[0]
print(slot_outputs)
with tf.variable_scope('slot_loss'):
    if arg.use_crf:
        log_likelihood, trans_params = tf.contrib.crf.crf_log_likelihood(slot_outputs, slots, sequence_length)
        slot_loss = tf.reduce_mean(-log_likelihood)
    else:
        crossent = tf.nn.sparse_softmax_cross_entropy_with_logits(labels=slots_reshape, logits=slot_outputs)
        crossent = tf.reshape(crossent, slots_shape)
        slot_loss = tf.reduce_sum(crossent * slot_weights, 1)
        total_size = tf.reduce_sum(slot_weights, 1)
        total_size += 1e-12
        slot_loss = slot_loss / total_size

intent_output = training_outputs[1]
with tf.variable_scope('intent_loss'):
    crossent = tf.nn.sparse_softmax_cross_entropy_with_logits(labels=intent, logits=intent_output)
    intent_loss = tf.reduce_sum(crossent) / tf.cast(arg.batch_size, tf.float32)

params = tf.trainable_variables()
# learning rate decay
learning_rate = tf.train.exponential_decay(arg.learning_rate, global_step, arg.decay_steps, arg.decay_rate,
                                           staircase=False)
if arg.learning_rate_decay:
    opt = tf.train.AdamOptimizer(learning_rate)
else:
    opt = tf.train.AdamOptimizer(arg.learning_rate)
intent_params = []
slot_params = []
for p in params:
    if not 'slot_' in p.name:
        intent_params.append(p)
    if 'slot_' in p.name or 'bidirectional_rnn' in p.name or 'embedding' in p.name:
        slot_params.append(p)  

gradients_slot = tf.gradients(slot_loss, slot_params)
gradients_intent = tf.gradients(intent_loss, intent_params)
clipped_gradients_slot, norm_slot = tf.clip_by_global_norm(gradients_slot, 5.0)
clipped_gradients_intent, norm_intent = tf.clip_by_global_norm(gradients_intent, 5.0)
gradient_norm_slot = norm_slot
gradient_norm_intent = norm_intent
update_slot = opt.apply_gradients(zip(clipped_gradients_slot, slot_params))
update_intent = opt.apply_gradients(zip(clipped_gradients_intent, intent_params), global_step=global_step)
training_outputs = [global_step, slot_loss, update_intent, update_slot, gradient_norm_intent, gradient_norm_slot]
inputs = [input_data, sequence_length, slots, slot_weights, intent]

# Create Inference Model
with tf.variable_scope('model', reuse=True):
    inference_outputs = createModel(input_data, len(in_vocab['vocab']), sequence_length, slots,
                                    len(slot_vocab['vocab']),
                                    len(intent_vocab['vocab']), layer_size=arg.layer_size, isTraining=False)
# slot output
if arg.use_crf:
    inference_slot_output, pred_scores = tf.contrib.crf.crf_decode(inference_outputs[0], trans_params, sequence_length)
else:
    inference_slot_output = tf.nn.softmax(inference_outputs[0], name='slot_output')
# intent output

inference_intent_output = tf.nn.softmax(inference_outputs[1], name='intent_output')

inference_outputs = [inference_intent_output, inference_slot_output]
inference_inputs = [input_data, sequence_length]

logging.basicConfig(format='%(asctime)s : %(levelname)s : %(message)s', level=logging.INFO)

saver = tf.train.Saver()
# gpu setting
gpu_options=tf.GPUOptions(per_process_gpu_memory_fraction=0.2)
config = tf.ConfigProto(log_device_placement=True,allow_soft_placement=True,gpu_options=gpu_options)
config.gpu_options.allow_growth = True
# Start Training
with tf.Session(config=config) as sess:
    sess.run(tf.global_variables_initializer()) 
    logging.info('Training Start') 

    epochs = 0
    loss = 0.0
    data_processor = None
    line = 0
    num_loss = 0
    step = 0
    no_improve = 0

    # variables to store highest values among epochs, only use 'valid_err' for now
    valid_slot = 0
    test_slot = 0
    valid_intent = 0
    test_intent = 0
    valid_err = 0  
    test_err = 0
    best_epoch_num = 0
    slot_f1_mean = 0
    intent_accuracy_mean = 0
    semantic_accuracy_mean = 0
    epoch_sum = 0
    while True:
        if data_processor == None:
            data_processor = DataProcessor(os.path.join(full_train_path, arg.input_file),
                                           os.path.join(full_train_path, arg.slot_file),
                                           os.path.join(full_train_path, arg.intent_file), in_vocab, slot_vocab,
                                           intent_vocab)
        in_data, slot_data, slot_weight, length, intents, _, _, _ = data_processor.get_batch(arg.batch_size)
        feed_dict = {input_data.name: in_data, slots.name: slot_data, slot_weights.name: slot_weight,
                     sequence_length.name: length, intent.name: intents}
        ret = sess.run(training_outputs, feed_dict)
        loss += np.mean(ret[1])

        line += arg.batch_size
        step = ret[0]
        num_loss += 1

        if data_processor.end == 1:
            arg.batch_size += arg.batch_size_add 
            line = 0
            data_processor.close() 
            data_processor = None
            epochs += 1 
            epoch_sum = epochs
            logging.info('Step: ' + str(step))
            logging.info('Epochs: ' + str(epochs))
            logging.info('Loss: ' + str(loss / num_loss))
            num_loss = 0
            loss = 0.0

            save_path = os.path.join(arg.model_path, '_step_' + str(step) + '_epochs_' + str(epochs) + '.ckpt')
            saver.save(sess, save_path)


            def valid(in_path, slot_path, intent_path):
                data_processor_valid = DataProcessor(in_path, slot_path, intent_path, in_vocab, slot_vocab,
                                                     intent_vocab)

                pred_intents = []
                correct_intents = []
                slot_outputs = []
                correct_slots = []
                input_words = []

                # used to gate
                gate_seq = []
                while True:
                    in_data, slot_data, slot_weight, length, intents, in_seq, slot_seq, intent_seq = data_processor_valid.get_batch(
                        arg.batch_size)
                    if len(in_data) <= 0:
                        break
                    feed_dict = {input_data.name: in_data, sequence_length.name: length}
                    ret = sess.run(inference_outputs, feed_dict)
                    for i in ret[0]:  
                        pred_intents.append(np.argmax(i))
                    for i in intents:
                        correct_intents.append(i)

                    pred_slots = ret[1].reshape((slot_data.shape[0], slot_data.shape[1], -1))
                    for p, t, i, l in zip(pred_slots, slot_data, in_data, length):
                        if arg.use_crf:
                            p = p.reshape([-1])
                        else:
                            p = np.argmax(p, 1)
                        tmp_pred = []
                        tmp_correct = []
                        tmp_input = []
                        for j in range(l):
                            tmp_pred.append(slot_vocab['rev'][p[j]])
                            tmp_correct.append(slot_vocab['rev'][t[j]])
                            tmp_input.append(in_vocab['rev'][i[j]])

                        slot_outputs.append(tmp_pred)  
                        correct_slots.append(tmp_correct)
                        input_words.append(tmp_input)

                    if data_processor_valid.end == 1:
                        break

                pred_intents = np.array(pred_intents)
                correct_intents = np.array(correct_intents)
                accuracy = (pred_intents == correct_intents)
                semantic_acc = accuracy
                accuracy = accuracy.astype(float)
                accuracy = np.mean(accuracy) * 100.0  

                index = 0
                for t, p in zip(correct_slots, slot_outputs):
                    # Process Semantic Error
                    if len(t) != len(p):
                        raise ValueError('Error!!')

                    for j in range(len(t)):
                        if p[j] != t[j]:
                            semantic_acc[index] = False
                            break
                    index += 1
                semantic_acc = semantic_acc.astype(float)
                semantic_acc = np.mean(semantic_acc) * 100.0

                f1, precision, recall = computeF1Score(correct_slots, slot_outputs)
                logging.info('slot f1: ' + str(f1))
                logging.info('intent accuracy: ' + str(accuracy))
                logging.info('semantic Acc(intent, slots are all correct): ' + str(semantic_acc))
                
                data_processor_valid.close()
                return f1, accuracy, semantic_acc, pred_intents, correct_intents, slot_outputs, correct_slots, input_words, gate_seq

            
            logging.info('Valid:')
            epoch_valid_slot, epoch_valid_intent, epoch_valid_err, valid_pred_intent, valid_correct_intent, valid_pred_slot, valid_correct_slot, valid_words, valid_gate = valid(
                os.path.join(full_valid_path, arg.input_file), os.path.join(full_valid_path, arg.slot_file),
                os.path.join(full_valid_path, arg.intent_file))

            logging.info('Test:')
            epoch_test_slot, epoch_test_intent, epoch_test_err, test_pred_intent, test_correct_intent, test_pred_slot, test_correct_slot, test_words, test_gate = valid(
                os.path.join(full_test_path, arg.input_file), os.path.join(full_test_path, arg.slot_file),
                os.path.join(full_test_path, arg.intent_file))
            
            logging.info('epoch_valid_slot :  {}'.format(epoch_valid_slot))
            logging.info('epoch_valid_intent : {}'.format(epoch_valid_intent))
            logging.info('epoch_valid_err: {}'.format(epoch_valid_err))
            logging.info('epoch_test_slot :  {}'.format(epoch_test_slot))
            logging.info('epoch_test_intent : {}'.format(epoch_test_intent))
            logging.info('epoch_test_err: {}'.format(epoch_test_err))
            if epoch_test_err <= test_err:
                no_improve += 1
            else:
                best_epoch_num = epochs
                test_err = epoch_test_err

                # logging.info('new best epoch number: Epoch Number: {}'.format(best_epoch_num))
                # logging.info('new best score: Semantic Acc: {}'.format(epoch_test_err))
                no_improve = 0

            if test_err > 0:
                logging.info('best epoch_num :  {}'.format(best_epoch_num))
                logging.info('best score : {}'.format(test_err))
            if epochs == arg.max_epochs:
                break

            if arg.early_stop == True:
                if no_improve > arg.patience:
                    break
