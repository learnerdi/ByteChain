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
import urllib.request

import tornado.web
import tornado.websocket
import tornado.ioloop
import tornado.httpclient
import tornado.gen
import tornado.escape

import setting
import tree
# import node
# import leader
import database

import ecdsa

frozen_block_hash = '0'*64
frozen_chain = ['0'*64]
frozen_nodes_in_chain = {}
highest_block_hash = None
recent_longest = []
nodes_in_chain = {}


def longest_chain(from_hash = '0'*64):
    conn = database.get_conn2()
    c = conn.cursor()
    c.execute("SELECT * FROM chain WHERE prev_hash = ?", (from_hash,))
    roots = c.fetchall()

    chains = []
    prev_hashs = []
    for root in roots:
        # chains.append([root.hash])
        chains.append([root])
        # print(root)
        block_hash = root[1]
        prev_hashs.append(block_hash)

    t0 = time.time()
    n = 0
    while True:
        if prev_hashs:
            prev_hash = prev_hashs.pop(0)
        else:
            break

        c.execute("SELECT * FROM chain WHERE prev_hash = ?", (prev_hash,))
        leaves = c.fetchall()
        n += 1
        if len(leaves) > 0:
            block_height = leaves[0][3]
            if block_height % 1000 == 0:
                print('longest height', block_height)
            for leaf in leaves:
                for chain in chains:
                    prev_block = chain[-1]
                    prev_block_hash = prev_block[1]
                    # print(prev_block_hash)
                    if prev_block_hash == prev_hash:
                        forking_chain = copy.copy(chain)
                        # chain.append(leaf.hash)
                        chain.append(leaf)
                        chains.append(forking_chain)
                        break
                leaf_hash = leaf[1]
                if leaf_hash not in prev_hashs and leaf_hash:
                    prev_hashs.append(leaf_hash)
    t1 = time.time()
    # print(tree.current_port, "query time", t1-t0, n)

    longest = []
    for i in chains:
        # print(i)
        if not longest:
            longest = i
        if len(longest) < len(i):
            longest = i
    return longest


messages_out = []
def looping():
    global messages_out
    # global recent_longest
    # print(messages_out)

    while messages_out:
        message = messages_out.pop(0)
        tree.forward(message)

        if MinerHandler.child_miners:
            msg = tornado.escape.json_encode(message)
            for i in MinerHandler.child_miners:
                i.write_message(msg)

    # if recent_longest:
    #     leaders = [i for i in recent_longest if i['timestamp'] < time.time()-setting.MAX_MESSAGE_DELAY_SECONDS and i['timestamp'] > time.time()-setting.MAX_MESSAGE_DELAY_SECONDS - setting.BLOCK_INTERVAL_SECONDS*20][-setting.LEADERS_NUM:]
    #     # print(leaders)
    #     leader.update(leaders)

    tornado.ioloop.IOLoop.instance().call_later(1, looping)


def miner_looping():
    global messages_out

    while messages_out:
        message = messages_out.pop(0)
        if MinerConnector.node_miner:
            MinerConnector.node_miner.write_message(tornado.escape.json_encode(message))

    tornado.ioloop.IOLoop.instance().call_later(1, miner_looping)

nodes_to_fetch = []
highest_block_height = 0
last_highest_block_height = 0
hash_proofs = set()
last_hash_proofs = set()
subchains_block = {}
last_subchains_block = {}

@tornado.gen.coroutine
def new_chain_block(seq):
    # global frozen_block_hash
    global nodes_to_fetch
    # global recent_longest
    global worker_thread_mining
    global highest_block_height
    global last_highest_block_height
    global hash_proofs
    global last_hash_proofs
    global subchains_block
    global last_subchains_block
    msg_header, block_hash, prev_hash, height, nonce, difficulty, identity, data, timestamp, nodeno, msg_id = seq
    # validate
    # check difficulty

    conn = database.get_conn()
    c = conn.cursor()
    try:
        c.execute("INSERT INTO chain (hash, prev_hash, height, nonce, difficulty, identity, timestamp, data) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (block_hash, prev_hash, height, nonce, difficulty, identity, timestamp, tornado.escape.json_encode(data)))
    except Exception as e:
        print("new_chain_block Error: %s" % e)
    conn.commit()

    # if prev_hash != '0'*64:
    #     prev_block = database.connection.get("SELECT * FROM chain"+tree.current_port+" WHERE hash = %s", prev_hash)
    #     if not prev_block:
    #         no, pk = identity.split(":")
    #         if int(no) not in nodes_to_fetch:
    #             nodes_to_fetch.append(int(no))
    #         worker_thread_mining = False

    print(highest_block_height, height, identity)
    if highest_block_height + 1 < height:
        # no, pk = identity.split(":")
        # if int(no) not in nodes_to_fetch:
        nodes_to_fetch.append(int(nodeno))
        worker_thread_mining = False
    elif highest_block_height + 1 == height:
        highest_block_height = height

    if last_highest_block_height != highest_block_height:
        last_subchains_block = subchains_block
        subchains_block = {}
        if last_highest_block_height + 1 == highest_block_height:
            last_hash_proofs = hash_proofs
        else:
            last_hash_proofs = set()
        hash_proofs = set()
        last_highest_block_height = highest_block_height

@tornado.gen.coroutine
def new_chain_proof(seq):
    global nodes_to_fetch
    # global recent_longest
    global highest_block_height
    global last_highest_block_height
    global hash_proofs
    global last_hash_proofs

    msg_header, block_hash, prev_hash, height, nonce, difficulty, identity, data, timestamp, msg_id = seq
    # validate
    # check difficulty
    print('new_chain_proof', highest_block_height, height)

    conn = database.get_conn()
    c = conn.cursor()
    try:
        c.execute("INSERT INTO proof (hash, prev_hash, height, nonce, difficulty, identity, timestamp, data) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (block_hash, prev_hash, height, nonce, difficulty, identity, timestamp, tornado.escape.json_encode(data)))
    except Exception as e:
        print("new_chain_block Error: %s" % e)
    conn.commit()

    print(highest_block_height, height, identity)
    # if highest_block_height + 1 < height:
    #     no, pk = identity.split(":")
    #     if int(no) not in nodes_to_fetch:
    #         nodes_to_fetch.append(int(no))

    if last_highest_block_height != highest_block_height:
        if last_highest_block_height + 1 == highest_block_height:
            last_hash_proofs = hash_proofs
        else:
            last_hash_proofs = set()
        hash_proofs = set()
        last_highest_block_height = highest_block_height

    if highest_block_height + 1 == height:
        hash_proofs.add(tuple([block_hash, height]))

    print('hash_proofs', hash_proofs)
    print('last_hash_proofs', last_hash_proofs)

@tornado.gen.coroutine
def new_subchain_block(seq):
    global subchains_block
    # global last_subchains_block
    msg_header, block_hash, prev_hash, sender, receiver, height, data, timestamp, signature = seq
    # validate
    # need to ensure current subchains_block[sender] is the ancestor of block_hash
    subchains_block[sender] = block_hash

    conn = database.get_conn()
    c = conn.cursor()
    try:
        c.execute("INSERT INTO subchains (hash, prev_hash, sender, receiver, height, timestamp, data, signature) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (block_hash, prev_hash, sender, receiver, height, timestamp, tornado.escape.json_encode(data), signature))
    except Exception as e:
        print("new_subchain_block Error: %s" % e)
    conn.commit()


class GetHighestBlockHandler(tornado.web.RequestHandler):
    def get(self):
        global highest_block_hash
        self.finish({"hash": highest_block_hash})

class GetBlockHandler(tornado.web.RequestHandler):
    def get(self):
        block_hash = self.get_argument("hash")
        conn = database.get_conn()
        c = conn.cursor()
        c.execute("SELECT * FROM chain WHERE hash = ?", (block_hash,))
        block = c.fetchone()
        self.finish({"block": block[1:]})

class GetHighestSubchainBlockHandler(tornado.web.RequestHandler):
    def get(self):
        # global highest_block_hash
        sender = self.get_argument('sender')
        conn = database.get_conn()
        c = conn.cursor()
        c.execute("SELECT * FROM subchains WHERE sender = ? ORDER BY height DESC LIMIT 1", (sender,))
        block = c.fetchone()
        if block:
            self.finish({"hash": block[1]})
        else:
            self.finish({"hash": '0'*64})

class GetSubchainBlockHandler(tornado.web.RequestHandler):
    def get(self):
        block_hash = self.get_argument("hash")
        conn = database.get_conn()
        c = conn.cursor()
        c.execute("SELECT * FROM subchains WHERE hash = ?", (block_hash,))
        block = c.fetchone()
        if block:
            self.finish({"block": block[1:]})
        else:
            self.finish({"block": None})

def fetch_chain(nodeid):
    print(tree.current_nodeid, 'fetch chain', nodeid)
    host, port = tree.current_host, tree.current_port
    prev_nodeid = None
    while True:
        try:
            response = urllib.request.urlopen("http://%s:%s/get_node?nodeid=%s" % (host, port, nodeid))
        except:
            break
        result = tornado.escape.json_decode(response.read())
        host, port = result['address']
        if result['nodeid'] == result['current_nodeid']:
            break
        if prev_nodeid == result['current_nodeid']:
            break
        prev_nodeid = result['current_nodeid']
        print('result >>>>>', nodeid, result)

    try:
        response = urllib.request.urlopen("http://%s:%s/get_highest_block" % (host, port))
    except:
        return
    result = tornado.escape.json_decode(response.read())
    block_hash = result['hash']
    if not block_hash:
        return
    # validate

    print("get highest block", block_hash)
    while block_hash != '0'*64:
        conn = database.get_conn2()
        c = conn.cursor()
        c.execute("SELECT * FROM chain WHERE hash = ?", (block_hash,))
        block = c.fetchone()
        if block:
            if block[3] % 1000 == 0: #.height
                print('block height', block[3])#.height
            block_hash = block[2]#.prev_hash
            continue
        try:
            response = urllib.request.urlopen("http://%s:%s/get_block?hash=%s" % (host, port, block_hash))
        except:
            continue
        result = tornado.escape.json_decode(response.read())
        block = result["block"]
        # if block['height'] % 1000 == 0:
        print("fetch block", block[0])
        block_hash = block[1]
        conn = database.get_conn2()
        c = conn.cursor()
        try:
            c.execute("INSERT INTO chain (hash, prev_hash, height, nonce, difficulty, identity, timestamp, data) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (block[0], block[1], block[2], block[3], block[4], block[5], block[6], block[7]))
        except Exception as e:
            print("fetch_chain Error: %s" % e)
        conn.commit()

nonce = 0
def mining():
    global nonce
    global frozen_block_hash
    global frozen_chain
    global frozen_nodes_in_chain
    global recent_longest
    global nodes_in_chain
    global highest_block_hash
    global highest_block_height
    global messages_out
    # global hash_proofs
    global last_hash_proofs
    # global subchains_block
    global last_subchains_block

    longest = longest_chain(frozen_block_hash)
    if longest:
        highest_block_hash = longest[-1][1]#.hash
        if highest_block_height < longest[-1][3]:#.height
            highest_block_height = longest[-1][3]#.height

    if len(longest) > setting.FROZEN_BLOCK_NO:
        frozen_block_hash = longest[-setting.FROZEN_BLOCK_NO][2]#.prev_hash
        frozen_longest = longest[:-setting.FROZEN_BLOCK_NO]
        recent_longest = longest[-setting.FROZEN_BLOCK_NO:]
    else:
        frozen_longest = []
        recent_longest = longest

    for i in frozen_longest:
        print("frozen longest", i[3]) #.height
        data = tornado.escape.json_decode(i[8])#.data
        frozen_nodes_in_chain.update(data.get("nodes", {}))
        if i[1] not in frozen_chain:#.hash
            frozen_chain.append(i[1])#.hash

    nodes_in_chain = copy.copy(frozen_nodes_in_chain)
    for i in recent_longest:
        data = tornado.escape.json_decode(i[8])#.data
        # for j in data.get("nodes", {}):
        #     print("recent longest", i.height, j, data["nodes"][j])
        nodes_in_chain.update(data.get("nodes", {}))

    # if tree.current_nodeid not in nodes_in_chain and tree.parent_node_id_msg:
    #     tree.forward(tree.parent_node_id_msg)
    #     print(tree.current_port, 'parent_node_id_msg', tree.parent_node_id_msg)

    if len(recent_longest) > setting.BLOCK_DIFFICULTY_CYCLE:
        height_in_cycle = recent_longest[-1][3] % setting.BLOCK_DIFFICULTY_CYCLE #.height
        timecost = recent_longest[-1-height_in_cycle][7] - recent_longest[-height_in_cycle-setting.BLOCK_DIFFICULTY_CYCLE][7]
        difficulty = 2**248 * timecost / (setting.BLOCK_INTERVAL_SECONDS * setting.BLOCK_DIFFICULTY_CYCLE)#.timestamp
    else:
        difficulty = 2**248

    now = int(time.time())
    last_synctime = now - now % setting.NETWORK_SPREADING_SECONDS - setting.NETWORK_SPREADING_SECONDS
    nodes_to_update = {}
    for nodeid in tree.nodes_pool:
        if tree.nodes_pool[nodeid][1] < last_synctime:
            if nodeid not in nodes_in_chain or nodes_in_chain[nodeid][1] < tree.nodes_pool[nodeid][1]:
                # print("nodes_to_update", nodeid, nodes_in_chain[nodeid][1], tree.nodes_pool[nodeid][1], last_synctime)
                nodes_to_update[nodeid] = tree.nodes_pool[nodeid]

    # nodes_in_chain.update(tree.nodes_pool)
    # tree.nodes_pool = nodes_in_chain
    # print(tree.nodes_pool)
    # print(nodes_to_update)

    # print(frozen_block_hash, longest)
    nodeno = str(tree.nodeid2no(tree.current_nodeid))
    pk = base64.b32encode(tree.node_sk.get_verifying_key().to_string()).decode("utf8")
    if longest:
        prev_hash = longest[-1][1]#.hash
        height = longest[-1][3]#.height
        identity = longest[-1][6]#.identity
        data = tornado.escape.json_decode(longest[-1][8])#.data
        # print(tree.dashboard_port, "new difficulty", new_difficulty, "height", height)

        # print("%s:%s" % (nodeno, pk))
        # leaders = [i for i in longest if i['timestamp'] < time.time()-setting.MAX_MESSAGE_DELAY_SECONDS and i['timestamp'] > time.time()-setting.MAX_MESSAGE_DELAY_SECONDS - setting.BLOCK_INTERVAL_SECONDS*20][-setting.LEADERS_NUM:]
        # if "%s:%s" % (nodeno, pk) in [i.identity for i in leaders]:
        #     # tornado.ioloop.IOLoop.instance().call_later(1, mining)
        #     # print([i.identity for i in leaders])
        #     return

    else:
        prev_hash, height, data, identity = '0'*64, 0, {}, ":"
    new_difficulty = int(math.log(difficulty, 2))

    # data = {"nodes": {k:list(v) for k, v in tree.nodes_pool.items()}}
    data["nodes"] = nodes_to_update
    data["proofs"] = list([list(p) for p in last_hash_proofs])
    data["subchains"] = last_subchains_block
    data_json = tornado.escape.json_encode(data)

    # new_identity = "%s@%s:%s" % (tree.current_nodeid, tree.current_host, tree.current_port)
    # new_identity = "%s:%s" % (nodeno, pk)
    new_identity = pk
    new_timestamp = time.time()
    for i in range(100):
        block_hash = hashlib.sha256((prev_hash + str(height+1) + str(nonce) + str(new_difficulty) + new_identity + data_json + str(new_timestamp)).encode('utf8')).hexdigest()
        if int(block_hash, 16) < difficulty:
            if longest:
                print(tree.current_port, 'height', height, 'nodeid', tree.current_nodeid, 'nonce_init', tree.nodeid2no(tree.current_nodeid), 'timecost', longest[-1][7] - longest[0][7])#.timestamp

            message = ["NEW_CHAIN_BLOCK", block_hash, prev_hash, height+1, nonce, new_difficulty, new_identity, data, new_timestamp, nodeno, uuid.uuid4().hex]
            messages_out.append(message)
            # print(tree.current_port, "mining", nonce, block_hash)
            nonce = 0
            break

        if int(block_hash, 16) < difficulty*2:
            # if longest:
            #     print(tree.current_port, 'height', height, 'nodeid', tree.current_nodeid, 'nonce_init', tree.nodeid2no(tree.current_nodeid), 'timecost', longest[-1][7] - longest[0][7])#.timestamp

            message = ["NEW_CHAIN_PROOF", block_hash, prev_hash, height+1, nonce, new_difficulty, new_identity, data, new_timestamp, uuid.uuid4().hex]
            messages_out.append(message)

        nonce += 1

def validate():
    global highest_block_hash
    global highest_block_height
    global nodes_to_fetch
    global frozen_nodes_in_chain
    global frozen_chain
    global frozen_block_hash
    global worker_thread_mining

    c = 0
    for no in nodes_to_fetch:
        c += 1
        # no = nodes_to_fetch[0]
        nodeid = tree.nodeno2id(no)
        fetch_chain(nodeid)

    longest = longest_chain(frozen_block_hash)
    print(longest)
    if len(longest) >= setting.FROZEN_BLOCK_NO:
        frozen_block_hash = longest[-setting.FROZEN_BLOCK_NO][2]#.prev_hash
        frozen_longest = longest[:-setting.FROZEN_BLOCK_NO]
    #     recent_longest = longest[-setting.FROZEN_BLOCK_NO:]
    else:
        frozen_longest = []
    #     recent_longest = longest

    if longest:
        highest_block_hash = longest[-1][1] #.hash
        if highest_block_height < longest[-1][3]: #.height
            highest_block_height = longest[-1][3] #.height
    else:
        highest_block_hash = '0'*64

    for i in frozen_longest:
        if i[3] % 1000 == 0: #.height
            print("frozen longest reload", i[3])#.height
        data = tornado.escape.json_decode(i[8]) #.data
        frozen_nodes_in_chain.update(data.get("nodes", {}))
        if i[1] not in frozen_chain: #.hash
            frozen_chain.append(i[1]) #.hash

    for i in range(c):
        nodes_to_fetch.pop(0)
    if not nodes_to_fetch:
        worker_thread_mining = True


worker_thread_mining = False
def worker_thread():
    global frozen_block_hash
    global frozen_chain
    global frozen_nodes_in_chain
    # global recent_longest
    global nodes_in_chain
    global worker_thread_mining

    database.get_conn2(tree.current_name)

    while True:
        time.sleep(2)
        if worker_thread_mining and setting.MINING:
            # print('chain mining')
            mining()
            continue

        if tree.current_nodeid is None:
            continue

        # print('chain validation')
        # if tree.current_nodeid:
        #     fetch_chain(tree.current_nodeid[:-1])
        validate()

    # mining_task = tornado.ioloop.PeriodicCallback(mining, 1000) # , jitter=0.5
    # mining_task.start()
    # print(tree.current_port, "miner")


# @tornado.gen.coroutine
# def main():
#     tornado.ioloop.IOLoop.instance().call_later(1, looping)

class MinerHandler(tornado.websocket.WebSocketHandler):
    child_miners = set()

    def check_origin(self, origin):
        return True

    def open(self):
        if self not in MinerHandler.child_miners:
            MinerHandler.child_miners.add(self)
        print(tree.current_port, "MinerHandler miner connected")

    def on_close(self):
        print(tree.current_port, "MinerHandler disconnected")
        if self in MinerHandler.child_miners:
            MinerHandler.child_miners.remove(self)

    @tornado.gen.coroutine
    def on_message(self, message):
        seq = tornado.escape.json_decode(message)
        if seq[0] == "NEW_CHAIN_BLOCK":
            print("MinerHandler NEW_CHAIN_BLOCK", seq)
            new_chain_block(seq)

        elif seq[0] == "NEW_CHAIN_PROOF":
            print("MinerHandler NEW_CHAIN_PROOF", seq)
            new_chain_proof(seq)

        elif seq[0] == "NEW_SUBCHAIN_BLOCK":
            print("MinerHandler NEW_SUBCHAIN_BLOCK", seq)
            new_subchain_block(seq)

        tree.forward(seq)


# connector to parent node
class MinerConnector(object):
    """Websocket Client"""
    node_miner = None

    def __init__(self, host, port):
        self.host = host
        self.port = port
        self.ws_uri = "ws://%s:%s/miner" % (self.host, self.port)
        self.conn = None
        self.connect()

    def connect(self):
        tornado.websocket.websocket_connect(self.ws_uri,
                                callback = self.on_connect,
                                on_message_callback = self.on_message,
                                connect_timeout = 1000.0,
                                ping_timeout = 600.0
                            )

    def close(self):
        self.conn.close()
        MinerConnector.node_miner = None
        print('MinerConnector close')

    @tornado.gen.coroutine
    def on_connect(self, future):
        try:
            self.conn = future.result()
            MinerConnector.node_miner = self.conn
            print('on_connect', MinerConnector)
        except:
            tornado.ioloop.IOLoop.instance().call_later(1.0, self.connect)

    @tornado.gen.coroutine
    def on_message(self, message):
        # global current_branch
        # global current_nodeid
        # global node_parents
        # global node_neighborhoods
        # global nodes_pool
        # global parent_node_id_msg

        if message is None:
            print("MinerConnector reconnect ...")
            tornado.ioloop.IOLoop.instance().call_later(1.0, self.connect)
            return

        seq = tornado.escape.json_decode(message)
        if seq[0] == "NEW_CHAIN_BLOCK":
            print("MinerConnector got NEW_CHAIN_BLOCK", seq)
            new_chain_block(seq)

        elif seq[0] == "NEW_CHAIN_PROOF":
            print("MinerConnector got NEW_CHAIN_PROOF", seq)
            new_chain_proof(seq)

        elif seq[0] == "NEW_SUBCHAIN_BLOCK":
            print("MinerConnector got NEW_SUBCHAIN_BLOCK", seq)
            new_subchain_block(seq)


if __name__ == '__main__':
    # print("run python node.py pls")
    # tree.current_port = "8001"
    # longest_chain2()
    # longest_chain()

    tornado.ioloop.IOLoop.instance().call_later(1, miner_looping)

    parser = argparse.ArgumentParser(description="node.py --name=[miner name]")
    parser.add_argument('--name')
    parser.add_argument('--host')
    parser.add_argument('--port')

    args = parser.parse_args()
    if not args.name:
        print('--name reqired')
        sys.exit()
    tree.current_name = args.name
    tree.current_nodeid = 0
    tree.current_host = args.host
    tree.current_port = args.port
    sk_filename = "%s.pem" % tree.current_name
    if os.path.exists(sk_filename):
        tree.node_sk = ecdsa.SigningKey.from_pem(open(sk_filename).read())
    else:
        tree.node_sk = ecdsa.SigningKey.generate(curve=ecdsa.NIST256p)
        open(sk_filename, "w").write(bytes.decode(tree.node_sk.to_pem()))
    database.main()

    worker_thread_mining = True
    MinerConnector(tree.current_host, tree.current_port)
    worker_threading = threading.Thread(target=worker_thread)
    worker_threading.start()

    tornado.ioloop.IOLoop.instance().start()
    worker_threading.join()