// SPDX-License-Identifier: MIT
pragma solidity ^0.8.19;

import "@openzeppelin/contracts/utils/ReentrancyGuard.sol";
import "@openzeppelin/contracts/access/Ownable.sol";
import "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";

// Balancer V3 Flash Loan Interface
interface IBalancerVault {
    function flashLoan(
        address[] memory tokens,
        uint256[] memory amounts,
        bytes memory userData
    ) external;
}

// Uniswap V2-style Router Interface (used for Uniswap, PancakeSwap, BaseSwap)
interface IUniswapV2Router {
    function swapExactTokensForTokens(
        uint amountIn,
        uint amountOutMin,
        address[] calldata path,
        address to,
        uint deadline
    ) external returns (uint[] memory amounts);

    function getAmountsOut(uint amountIn, address[] calldata path)
        external
        view
        returns (uint[] memory amounts);
}

contract FlashLoanArbitrage is ReentrancyGuard, Ownable {
    using SafeERC20 for IERC20;

    // Balancer V3 Vault address on Base
    IBalancerVault public constant BALANCER_VAULT =
        IBalancerVault(0xBA12222222228d8Ba445958a75a0704d566BF2C8);

    // DEX Router addresses on Base
    address public constant UNISWAP_ROUTER =
        0x4752ba5DBc23f44D87826276BF6Fd6b1C372aD24;
    address public constant PANCAKE_ROUTER =
        0x8cFe327CEc66d1C090Dd72bd0FF11d690C33a2Eb;
    // ADDED: BaseSwap Router
    address public constant BASESWAP_ROUTER =
        0x327Df1E6de05895d2ab08513aaDD9313Fe505d86;

    // Supported stablecoins on Base
    address public constant USDC = 0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913;
    address public constant USDbC = 0xd9aAEc86B65D86f6A7B5B1b0c42FFA531710b6CA;

    // Events
    event ArbitrageExecuted(
        address indexed token,
        uint256 flashLoanAmount,
        uint256 profit,
        address indexed user
    );
    event FlashLoanCompleted(
        address indexed token,
        uint256 amount,
        uint256 fee
    );

    // Struct to track arbitrage parameters
    struct ArbitrageParams {
        address tokenA; // Token to arbitrage
        address tokenB; // Stable token (USDC/USDbC)
        uint256 flashLoanAmount; // Amount to flash loan
        address dexLowPrice; // DEX with lower price (router address)
        address dexHighPrice; // DEX with higher price (router address)
        address user; // User who initiated the arbitrage
        uint256 minProfit; // Minimum profit threshold
    }

    // Mapping to track ongoing arbitrages
    mapping(address => bool) public arbitrageInProgress;

    modifier onlyWhenNotInProgress(address token) {
        require(
            !arbitrageInProgress[token],
            "Arbitrage already in progress for this token"
        );
        _;
    }

    constructor() Ownable(msg.sender) {}

    /**
     * @dev Execute flash loan arbitrage
     * @param params Arbitrage parameters
     */
    function executeArbitrage(ArbitrageParams calldata params)
        external
        onlyWhenNotInProgress(params.tokenA)
        nonReentrant
    {
        require(params.tokenA != address(0), "Invalid token address");
        require(
            params.tokenB == USDC || params.tokenB == USDbC,
            "Only USDC/USDbC supported as stable"
        );
        require(params.flashLoanAmount > 0, "Flash loan amount must be > 0");
        require(params.user != address(0), "Invalid user address");

        // Mark arbitrage as in progress
        arbitrageInProgress[params.tokenA] = true;

        // Prepare flash loan
        address[] memory tokens = new address[](1);
        uint256[] memory amounts = new uint256[](1);

        tokens[0] = params.tokenB; // Borrow stable coin
        amounts[0] = params.flashLoanAmount;

        // Encode arbitrage parameters for the callback
        bytes memory userData = abi.encode(params);

        // Execute flash loan
        BALANCER_VAULT.flashLoan(tokens, amounts, userData);
    }

    /**
     * @dev Balancer flash loan callback
     */
    function receiveFlashLoan(
        address[] memory tokens,
        uint256[] memory amounts,
        uint256[] memory feeAmounts,
        bytes memory userData
    ) external {
        require(
            msg.sender == address(BALANCER_VAULT),
            "Only Balancer Vault can call this"
        );

        // Decode parameters
        ArbitrageParams memory params = abi.decode(
            userData,
            (ArbitrageParams)
        );

        uint256 stableAmount = amounts[0];
        uint256 flashLoanFee = feeAmounts[0];
        uint256 totalRepayAmount = stableAmount + flashLoanFee;

        // Execute arbitrage trades
        uint256 profit = _executeArbitrageTrades(params, stableAmount);

        // Ensure we have enough to repay the flash loan + fee
        uint256 contractBalance = IERC20(params.tokenB).balanceOf(
            address(this)
        );
        require(
            contractBalance >= totalRepayAmount,
            "Insufficient funds to repay flash loan"
        );

        // Recalculate profit after ensuring repayment amount is covered
        uint256 netProfit = contractBalance - totalRepayAmount;

        // Ensure minimum profit requirement
        require(netProfit >= params.minProfit, "Profit below minimum threshold");

        // Transfer profit to user
        if (netProfit > 0) {
            IERC20(params.tokenB).safeTransfer(params.user, netProfit);
        }

        // Repay flash loan + fee by approving the vault to pull the funds
        IERC20(params.tokenB).approve(address(BALANCER_VAULT), totalRepayAmount);

        // Mark arbitrage as completed
        arbitrageInProgress[params.tokenA] = false;

        emit ArbitrageExecuted(
            params.tokenA,
            stableAmount,
            netProfit,
            params.user
        );
        emit FlashLoanCompleted(params.tokenB, stableAmount, flashLoanFee);
    }

    /**
     * @dev Execute the arbitrage trades
     */
    function _executeArbitrageTrades(
        ArbitrageParams memory params,
        uint256 stableAmount
    ) internal returns (uint256) {
        // Step 1: Buy token on DEX with lower price
        uint256 tokenAmount = _swap(
            params.tokenB,
            params.tokenA,
            stableAmount,
            params.dexLowPrice
        );

        // Step 2: Sell token on DEX with higher price
        _swap(params.tokenA, params.tokenB, tokenAmount, params.dexHighPrice);

        // Profit is calculated in the callback after repayment
        return 1; // Return non-zero to indicate success
    }

    /**
     * @dev Generic swap function for any supported DEX
     */
    function _swap(
        address tokenIn,
        address tokenOut,
        uint256 amountIn,
        address router
    ) internal returns (uint256 amountOut) {
        IERC20(tokenIn).safeIncreaseAllowance(router, amountIn);

        address[] memory path = new address[](2);
        path[0] = tokenIn;
        path[1] = tokenOut;

        uint256[] memory amounts;
        if (
            router == UNISWAP_ROUTER ||
            router == PANCAKE_ROUTER ||
            router == BASESWAP_ROUTER // ADDED: BaseSwap support
        ) {
            amounts = IUniswapV2Router(router).swapExactTokensForTokens(
                amountIn,
                0, // amountOutMin: 0 for simplicity
                path,
                address(this),
                block.timestamp
            );
        } else {
            revert("Unsupported DEX router");
        }

        return amounts[1];
    }

    /**
     * @dev Get expected arbitrage profit (view function for bot to check)
     */
    function calculateArbitrageProfit(
        address tokenA,
        address tokenB,
        uint256 amount,
        address dexLowPrice,
        address dexHighPrice
    ) external view returns (uint256 expectedProfit, bool profitable) {
        try
            this._simulateArbitrage(
                tokenA,
                tokenB,
                amount,
                dexLowPrice,
                dexHighPrice
            )
        returns (uint256 profit) {
            expectedProfit = profit;
            profitable = profit > 0;
        } catch {
            expectedProfit = 0;
            profitable = false;
        }
    }

    /**
     * @dev Simulate arbitrage for profit calculation
     */
    function _simulateArbitrage(
        address tokenA,
        address tokenB,
        uint256 amount,
        address dexLowPrice,
        address dexHighPrice
    ) internal view returns (uint256) {
        // Path for buying tokenA with tokenB
        address[] memory pathBuy = new address[](2);
        pathBuy[0] = tokenB;
        pathBuy[1] = tokenA;

        uint256 amountTokenA;
        if (
            dexLowPrice == UNISWAP_ROUTER ||
            dexLowPrice == PANCAKE_ROUTER ||
            dexLowPrice == BASESWAP_ROUTER // ADDED: BaseSwap support
        ) {
            uint256[] memory amountsOut = IUniswapV2Router(dexLowPrice)
                .getAmountsOut(amount, pathBuy);
            amountTokenA = amountsOut[1];
        } else {
            revert("Unsupported DEX router for simulation");
        }

        // Path for selling tokenA for tokenB
        address[] memory pathSell = new address[](2);
        pathSell[0] = tokenA;
        pathSell[1] = tokenB;

        uint256 finalAmountTokenB;
        if (
            dexHighPrice == UNISWAP_ROUTER ||
            dexHighPrice == PANCAKE_ROUTER ||
            dexHighPrice == BASESWAP_ROUTER // ADDED: BaseSwap support
        ) {
            uint256[] memory amountsOut = IUniswapV2Router(dexHighPrice)
                .getAmountsOut(amountTokenA, pathSell);
            finalAmountTokenB = amountsOut[1];
        } else {
            revert("Unsupported DEX router for simulation");
        }

        if (finalAmountTokenB > amount) {
            return finalAmountTokenB - amount;
        } else {
            return 0;
        }
    }

    /**
     * @dev Emergency withdraw function (only owner)
     */
    function emergencyWithdraw(address token, uint256 amount) external onlyOwner {
        IERC20(token).safeTransfer(owner(), amount);
    }

    /**
     * @dev Check if arbitrage is in progress for a token
     */
    function isArbitrageInProgress(address token)
        external
        view
        returns (bool)
    {
        return arbitrageInProgress[token];
    }
}