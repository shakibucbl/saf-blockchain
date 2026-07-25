import hashlib
import time
import json
from urllib.parse import urlparse
from flask import Flask, jsonify, request, render_template
import requests
import ecdsa
from ecdsa import SigningKey, SECP256k1
import binascii

class Wallet:
    def __init__(self):
        self.private_key = SigningKey.generate(curve=SECP256k1)
        self.public_key = self.private_key.get_verifying_key()

    @property
    def address(self):
        return binascii.hexlify(self.public_key.to_string()).decode()

class Blockchain:
    def __init__(self):
        self.chain = []
        self.current_transactions = []
        self.nodes = set()
        
        # জেনেসিস ব্লক তৈরি
        self.new_block(previous_hash="1", proof=100)

    def register_node(self, address):
        parsed_url = urlparse(address)
        if parsed_url.netloc:
            self.nodes.add(parsed_url.netloc)
        elif parsed_url.path:
            self.nodes.add(parsed_url.path)

    def valid_chain(self, chain):
        last_block = chain[0]
        current_index = 1

        while current_index < len(chain):
            block = chain[current_index]
            if block['previous_hash'] != self.hash(last_block):
                return False
            if not self.valid_proof(last_block['proof'], block['proof'], self.hash(last_block)):
                return False
            last_block = block
            current_index += 1
        return True

    def resolve_conflicts(self):
        neighbours = self.nodes
        new_chain = None
        max_length = len(self.chain)

        for node in neighbours:
            try:
                response = requests.get(f'http://{node}/chain')
                if response.status_code == 200:
                    length = response.json()['length']
                    chain = response.json()['chain']

                    if length > max_length and self.valid_chain(chain):
                        max_length = length
                        new_chain = chain
            except requests.exceptions.ConnectionError:
                continue # যদি কোনো নোড অফলাইন থাকে তবে স্কিপ করবে

        if new_chain:
            self.chain = new_chain
            return True
        return False

    def broadcast_new_block(self):
        """নতুন ব্লক তৈরি হলে পিটুপি নেটওয়ার্কের অন্যান্য নোডকে জানানো"""
        for node in self.nodes:
            try:
                requests.get(f'http://{node}/nodes/resolve')
            except requests.exceptions.ConnectionError:
                continue

    def new_block(self, proof, previous_hash=None):
        block = {
            'index': len(self.chain) + 1,
            'timestamp': time.time(),
            'transactions': self.current_transactions,
            'proof': proof,
            'previous_hash': previous_hash or self.hash(self.chain[-1]),
        }
        self.current_transactions = []
        self.chain.append(block)
        
        # ব্লক তৈরির সাথে সাথেই পিটুপি নেটওয়ার্কে ব্রডকাস্ট করা
        self.broadcast_new_block()
        return block

    def new_transaction(self, sender, recipient, amount, fee=1):
        self.current_transactions.append({
            'sender': sender,
            'recipient': recipient,
            'amount': amount,
            'fee': fee
        })
        return self.last_block['index'] + 1

    def get_balance(self, address):
        balance = 0
        for block in self.chain:
            for tx in block['transactions']:
                if tx['recipient'] == address:
                    balance += tx['amount']
                if tx['sender'] == address:
                    balance -= (tx['amount'] + tx.get('fee', 0))
        return balance

    @property
    def last_block(self):
        return self.chain[-1]

    @staticmethod
    def hash(block):
        block_string = json.dumps(block, sort_keys=True).encode()
        return hashlib.sha256(block_string).hexdigest()

    def proof_of_work(self, last_block):
        last_proof = last_block['proof']
        last_hash = self.hash(last_block)
        proof = 0
        while self.valid_proof(last_proof, proof, last_hash) is False:
            proof += 1
        return proof

    @staticmethod
    def valid_proof(last_proof, proof, last_hash):
        guess = f'{last_proof}{proof}{last_hash}'.encode()
        guess_hash = hashlib.sha256(guess).hexdigest()
        return guess_hash[:4] == "0000"

app = Flask(__name__)
blockchain = Blockchain()

@app.route('/', methods=['GET'])
def home():
    return render_template('index.html')

@app.route('/wallet/new', methods=['GET'])
def new_wallet():
    wallet = Wallet()
    response = {
        'private_key': binascii.hexlify(wallet.private_key.to_string()).decode(),
        'saf_address': wallet.address
    }
    return jsonify(response), 200

@app.route('/balance/<address>', methods=['GET'])
def wallet_balance(address):
    balance = blockchain.get_balance(address)
    return jsonify({'address': address, 'balance': balance}), 200

@app.route('/mine', methods=['GET'])
def mine():
    # মাইনিং করার আগে অন্য নোডগুলোর সাথে কনসেনসাস চেক করে নেওয়া
    blockchain.resolve_conflicts()

    last_block = blockchain.last_block
    proof = blockchain.proof_of_work(last_block)

    total_fees = sum(tx.get('fee', 0) for tx in blockchain.current_transactions)
    reward = 50 + total_fees

    blockchain.new_transaction(
        sender="0",  
        recipient="SAF_Miner_Wallet",
        amount=reward,
        fee=0
    )

    previous_hash = blockchain.hash(last_block)
    block = blockchain.new_block(proof, previous_hash)

    response = {
        'message': "New Block Forged and Broadcasted successfully!",
        'index': block['index'],
        'transactions': block['transactions'],
        'proof': block['proof'],
        'previous_hash': block['previous_hash'],
    }
    return jsonify(response), 200

@app.route('/transactions/new', methods=['POST'])
def new_transaction():
    values = request.get_json()
    required = ['sender', 'recipient', 'amount']
    if not all(k in values for k in required):
        return 'Missing values', 400

    fee = values.get('fee', 1)
    index = blockchain.new_transaction(values['sender'], values['recipient'], values['amount'], fee)
    response = {'message': f'Transaction will be added to Block #{index} (Fee: {fee} SAF)'}
    return jsonify(response), 201

@app.route('/chain', methods=['GET'])
def full_chain():
    response = {
        'chain': blockchain.chain,
        'length': len(blockchain.chain),
    }
    return jsonify(response), 200

@app.route('/nodes/register', methods=['POST'])
def register_nodes():
    values = request.get_json()
    nodes = values.get('nodes')
    if nodes is None:
        return "Error: Please supply a valid list of nodes", 400

    for node in nodes:
        blockchain.register_node(node)

    # নোড রেজিস্টার হওয়ার সাথেই স্বয়ংক্রিয়ভাবে চেইন সিংক করা
    blockchain.resolve_conflicts()

    response = {
        'message': 'New nodes have been added and chain synchronized',
        'total_nodes': list(blockchain.nodes),
    }
    return jsonify(response), 201

@app.route('/nodes/resolve', methods=['GET'])
def consensus():
    replaced = blockchain.resolve_conflicts()
    if replaced:
        response = {
            'message': 'Our chain was replaced by P2P consensus',
            'new_chain': blockchain.chain
        }
    else:
        response = {
            'message': 'Our chain is authoritative',
            'chain': blockchain.chain
        }
    return jsonify(response), 200

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)