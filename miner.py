from __future__ import print_function

import sys
import os
import math
import argparse
import time
import uuid
import hashlib
import copy
import base64
import threading
# import urllib.request
import secrets

import tornado.web
import tornado.websocket
import tornado.ioloop
import tornado.httpclient
import tornado.gen
import tornado.escape

import setting
import tree
# import node
import chain
import database

# import ecdsa
import eth_keys


# def longest_chain(from_hash = '0'*64):
#     conn = database.get_conn2()
#     c = conn.cursor()
#     c.execute("SELECT * FROM chain WHERE prev_hash = ?", (from_hash,))
#     roots = c.fetchall()

#     chains = []
#     prev_hashs = []
#     for root in roots:
#         # chains.append([root.hash])
#         chains.append([root])
#         # print(root)
#         block_hash = root[1]
#         prev_hashs.append(block_hash)

#     t0 = time.time()
#     n = 0
#     while True:
#         if prev_hashs:
#             prev_hash = prev_hashs.pop(0)
#         else:
#             break

#         c.execute("SELECT * FROM chain WHERE prev_hash = ?", (prev_hash,))
#         leaves = c.fetchall()
#         n += 1
#         if len(leaves) > 0:
#             block_height = leaves[0][3]
#             if block_height % 1000 == 0:
#                 print('longest height', block_height)
#             for leaf in leaves:
#                 for the_chain in chains:
#                     prev_block = the_chain[-1]
#                     prev_block_hash = prev_block[1]
#                     # print(prev_block_hash)
#                     if prev_block_hash == prev_hash:
#                         forking_chain = copy.copy(the_chain)
#                         # chain.append(leaf.hash)
#                         the_chain.append(leaf)
#                         chains.append(forking_chain)
#                         break
#                 leaf_hash = leaf[1]
#                 if leaf_hash not in prev_hashs and leaf_hash:
#                     prev_hashs.append(leaf_hash)
#     t1 = time.time()
#     # print(tree.current_port, "query time", t1-t0, n)

#     longest = []
#     for i in chains:
#         # print(i)
#         if not longest:
#             longest = i
#         if len(longest) < len(i):
#             longest = i
#     return longest


messages_out = []
def looping():
    global messages_out
    # print(messages_out)

    while messages_out:
        message = messages_out.pop(0)
        tree.forward(message)

    tornado.ioloop.IOLoop.instance().call_later(1, looping)


def miner_looping():
    global messages_out
    print("messages_out", len(messages_out))

    while messages_out:
        message = messages_out.pop(0)
        if tree.MinerConnector.node_miner:
            tree.MinerConnector.node_miner.write_message(tornado.escape.json_encode(message))

    tornado.ioloop.IOLoop.instance().call_later(1, miner_looping)


nonce = 0
def mining():
    global nonce
    global messages_out

    # TODO: move to validate
    # db = database.get_conn()
    # highest_block_hash = db.get(b'chain')
    # if highest_block_hash:
    #     highest_block_json = db.get(b'block%s' % highest_block_hash)
    #     if highest_block_json:
    #         highest_block = tornado.escape.json_decode(highest_block_json)

    #         if chain.highest_block_height < highest_block[chain.HEIGHT]:
    #             chain.highest_block_hash = highest_block_hash
    #             chain.highest_block_height = highest_block[chain.HEIGHT]

    # chain.nodes_in_chain = copy.copy(chain.frozen_nodes_in_chain)
    # for i in chain.recent_longest:
    #     data = tornado.escape.json_decode(i[8])#.data
    #     # for j in data.get("nodes", {}):
    #     #     print("recent longest", i.height, j, data["nodes"][j])
    #     chain.nodes_in_chain.update(data.get("nodes", {}))

    # if tree.current_nodeid not in nodes_in_chain and tree.parent_node_id_msg:
    #     tree.forward(tree.parent_node_id_msg)
    #     print(tree.current_port, 'parent_node_id_msg', tree.parent_node_id_msg)

    if len(chain.recent_longest):
        timecost = chain.recent_longest[0][chain.TIMESTAMP] - chain.recent_longest[-1][chain.TIMESTAMP]
        if timecost < 1:
            timecost = 1
        adjust = timecost / (setting.BLOCK_INTERVAL_SECONDS * setting.BLOCK_DIFFICULTY_CYCLE)
        if adjust > 4:
            adjust = 4
        if adjust < 1/4:
            adjust = 1/4
        difficulty = chain.recent_longest[0][chain.DIFFICULTY]
        block_difficulty = 2**difficulty * adjust
    else:
        block_difficulty = 2**248

    now = int(time.time())
    last_synctime = now - now % setting.NETWORK_SPREADING_SECONDS - setting.NETWORK_SPREADING_SECONDS
    nodes_to_update = {}
    for nodeid in tree.nodes_pool:
        if tree.nodes_pool[nodeid][1] < last_synctime:
            if nodeid not in chain.nodes_in_chain or chain.nodes_in_chain[nodeid][1] < tree.nodes_pool[nodeid][1]:
                # print("nodes_to_update", nodeid, nodes_in_chain[nodeid][1], tree.nodes_pool[nodeid][1], last_synctime)
                nodes_to_update[nodeid] = tree.nodes_pool[nodeid]

    # nodes_in_chain.update(tree.nodes_pool)
    # tree.nodes_pool = nodes_in_chain
    # print(tree.nodes_pool)
    # print(nodes_to_update)

    # print(frozen_block_hash, longest)
    nodeno = str(tree.nodeid2no(tree.current_nodeid))
    pk = tree.node_sk.public_key
    if chain.recent_longest:
        prev_hash = chain.recent_longest[0][chain.HASH]
        height = chain.recent_longest[0][chain.HEIGHT]
        identity = chain.recent_longest[0][chain.IDENTITY]

    else:
        prev_hash, height, identity = '0'*64, 0, ":"
    new_difficulty = int(math.log(block_difficulty, 2))

    data = {}
    data["nodes"] = nodes_to_update
    data["proofs"] = list([list(p) for p in chain.last_hash_proofs])
    data["subchains"] = chain.last_subchains_block
    data_json = tornado.escape.json_encode(data)

    # new_identity = "%s@%s:%s" % (tree.current_nodeid, tree.current_host, tree.current_port)
    # new_identity = "%s:%s" % (nodeno, pk)
    new_identity = pk.to_checksum_address()
    new_timestamp = time.time()
    if nonce % 1000 == 0:
        print(tree.current_port, 'mining', nonce, int(math.log(block_difficulty, 2)), height, len(chain.subchains_block), len(chain.last_subchains_block))
    for i in range(100):
        block_hash = hashlib.sha256((prev_hash + str(height+1) + str(nonce) + str(new_difficulty) + new_identity + data_json + str(new_timestamp)).encode('utf8')).hexdigest()
        if int(block_hash, 16) < block_difficulty:
            if chain.recent_longest:
                print(tree.current_port, 'height', height, 'nodeid', tree.current_nodeid, 'nonce_init', tree.nodeid2no(tree.current_nodeid), 'timecost', chain.recent_longest[-1][chain.TIMESTAMP] - chain.recent_longest[0][chain.TIMESTAMP])

            txid = uuid.uuid4().hex
            message = ['NEW_CHAIN_BLOCK', block_hash, prev_hash, height+1, nonce, new_difficulty, new_identity, data, new_timestamp, nodeno, txid]
            messages_out.append(message)
            print(tree.current_port, "mining", height+1, nonce, block_hash)
            nonce = 0

            db = database.get_conn()
            db.put(b'block%s' % block_hash.encode('utf8'), tornado.escape.json_encode([block_hash, prev_hash, height+1, nonce, new_difficulty, new_identity, data, new_timestamp, nodeno, txid]).encode('utf8'))
            db.put(b'chain', block_hash.encode('utf8'))

            break

        if int(block_hash, 16) < block_difficulty*2:
            # if longest:
            #     print(tree.current_port, 'height', height, 'nodeid', tree.current_nodeid, 'nonce_init', tree.nodeid2no(tree.current_nodeid), 'timecost', longest[-1][7] - longest[0][7])#.timestamp

            txid = uuid.uuid4().hex
            message = ['NEW_CHAIN_PROOF', block_hash, prev_hash, height+1, nonce, new_difficulty, new_identity, data, new_timestamp, txid]
            messages_out.append(message)

        nonce += 1

def validate():
    global nonce

    db = database.get_conn()
    highest_block_hash = db.get(b"chain")
    if highest_block_hash:
        block_json = db.get(b'block%s' % highest_block_hash)
        if block_json:
            block = tornado.escape.json_decode(block_json)
            highest_block_height = block[chain.HEIGHT]
    else:
        highest_block_hash = b'0'*64
        highest_block_height = 0

    print("validate nodes_to_fetch", chain.nodes_to_fetch)
    c = 0
    for nodeid in chain.nodes_to_fetch:
        c += 1
        new_chain_hash, new_chain_height = chain.fetch_chain(nodeid)
        print('validate', highest_block_hash, highest_block_height)
        print('validate', new_chain_hash, new_chain_height)
        if new_chain_height > highest_block_height:
            highest_block_hash = new_chain_hash
            highest_block_height = new_chain_height
            db.put(b"chain", highest_block_hash)

    block_hash = highest_block_hash
    chain.recent_longest = []
    for i in range(setting.BLOCK_DIFFICULTY_CYCLE):
        block_json = db.get(b'block%s' % block_hash)
        if block_json:
            block = tornado.escape.json_decode(block_json)
            block_hash = block[chain.PREV_HASH].encode('utf8')
            chain.recent_longest.append(block)
        else:
            break

    for i in range(c):
        chain.nodes_to_fetch.pop(0)
    if not chain.nodes_to_fetch:
        if setting.MINING:
            chain.worker_thread_mining = True
            nonce = 0


def worker_thread():
    while True:
        time.sleep(2)
        if chain.worker_thread_pause:
            continue

        if chain.worker_thread_mining:
            mining()
            continue

        if tree.current_nodeid is None:
            continue

        print('chain validation')
        validate()
        print('validation done')

    # mining_task = tornado.ioloop.PeriodicCallback(mining, 1000) # , jitter=0.5
    # mining_task.start()
    # print(tree.current_port, "miner")


if __name__ == '__main__':
    # print("run python node.py pls")
    # tree.current_port = "8001"

    tornado.ioloop.IOLoop.instance().call_later(1, miner_looping)

    parser = argparse.ArgumentParser(description="python3 node.py --name=<miner_name> [--host=<127.0.0.1>] [--port=<8001>]")
    parser.add_argument('--name')
    parser.add_argument('--host')
    parser.add_argument('--port')

    args = parser.parse_args()
    if not args.name:
        print('--name reqired')
        sys.exit()
    tree.current_name = args.name
    tree.current_host = args.host
    tree.current_port = args.port
    sk_filename = "miners/%s.key" % tree.current_name
    if os.path.exists(sk_filename):
        f = open(sk_filename, 'rb')
        raw_key = f.read(32)
        f.close()
        tree.node_sk = eth_keys.keys.PrivateKey(raw_key)
    else:
        raw_key = secrets.token_bytes(32)
        f = open(sk_filename, "wb")
        f.write(raw_key)
        f.close()
        tree.node_sk = eth_keys.keys.PrivateKey(raw_key)

    database.main()

    setting.MINING = True
    tree.MinerConnector(tree.current_host, tree.current_port)
    worker_threading = threading.Thread(target=worker_thread)
    worker_threading.start()

    tornado.ioloop.IOLoop.instance().start()
    # worker_threading.join()