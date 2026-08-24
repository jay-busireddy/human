// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

contract SafetyConsensus {
    address public owner;

    struct Proposal {
        bytes32 stateHash;
        bytes32 actionHash;
        bool exists;
    }
    struct Vote {
        bool safe;
        uint16 confidenceBps;
        bytes32 evidenceHash;
        bool exists;
    }

    mapping(bytes32 => Proposal) public proposals;
    mapping(bytes32 => mapping(address => Vote)) public votes;
    mapping(address => bool) public activeValidator;

    event ProposalSubmitted(bytes32 indexed proposalId, bytes32 stateHash, bytes32 actionHash);
    event VoteCast(bytes32 indexed proposalId, address indexed validator, bool safe, uint16 confidenceBps, bytes32 evidenceHash);
    event ValidatorStatus(address indexed validator, bool active);
    event Ping(bytes32 indexed id);

    constructor(address[] memory validators) {
        owner = msg.sender;
        for (uint i=0;i<validators.length;i++) {
            activeValidator[validators[i]] = true;
            emit ValidatorStatus(validators[i], true);
        }
    }

    function submitProposal(bytes32 proposalId, bytes32 stateHash, bytes32 actionHash) external {
        require(!proposals[proposalId].exists, "proposal exists");
        proposals[proposalId] = Proposal(stateHash, actionHash, true);
        emit ProposalSubmitted(proposalId,stateHash,actionHash);
    }

    function castVote(bytes32 proposalId, bool safe, uint16 confidenceBps, bytes32 evidenceHash) external {
        require(proposals[proposalId].exists, "missing proposal");
        require(activeValidator[msg.sender], "inactive validator");
        require(!votes[proposalId][msg.sender].exists, "duplicate vote");
        require(confidenceBps <= 10000, "bad confidence");
        votes[proposalId][msg.sender] = Vote(safe,confidenceBps,evidenceHash,true);
        emit VoteCast(proposalId,msg.sender,safe,confidenceBps,evidenceHash);
    }

    function setValidator(address v, bool active) external {
        require(msg.sender == owner, "owner only");
        activeValidator[v]=active;
        emit ValidatorStatus(v,active);
    }

    function ping(bytes32 id) external { emit Ping(id); }
}
